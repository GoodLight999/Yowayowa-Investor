from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, ClassVar, Literal

import pandas as pd
import yfinance as yf

from yowayowa.config import Settings
from yowayowa.domain import Fundamentals, LicenseClass, MetricPoint, MetricSeries, Provenance
from yowayowa.providers.base import ProviderDescriptor, enforce_provider_policy
from yowayowa.symbols import InputValidationError, normalize_currency, normalize_symbol


def _describe_exception(exc: BaseException) -> str:
    """Exception type plus message, so a recorded failure keeps its own reason."""

    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


@dataclass(frozen=True, slots=True)
class StatementFrameFailure:
    """One statement frame that yfinance raised on (outage, rate limit, schema)."""

    statement: str
    frequency: str
    reason: str

    def describe(self) -> str:
        return f"{self.statement}/{self.frequency}: {self.reason}"


@dataclass(slots=True)
class _StatementSample:
    """Loaded statement frames plus everything that kept a frame from being used.

    Failures, empty frames and unusable-schema frames are counted separately so
    "Yahoo did not answer" can never be reported as "this issuer has no
    financial statements" (AGENTS.md invariant 2: missing data is not zero).
    """

    frames: dict[str, list[pd.DataFrame]] = field(
        default_factory=lambda: {"yearly": [], "quarterly": []}
    )
    failures: list[StatementFrameFailure] = field(default_factory=list)
    attempted: int = 0
    empty_frames: int = 0
    schema_frames: int = 0
    unmapped_frames: int = 0

    @property
    def total_frames(self) -> int:
        """Every frame that was requested, including the ones that loaded."""

        return self.attempted


class YahooStatementFetchError(RuntimeError):
    """yfinance raised while one statement frame was being loaded."""

    def __init__(self, statement: str, frequency: str, cause: BaseException) -> None:
        self.statement = statement
        self.frequency = frequency
        self.cause = cause
        super().__init__(f"{statement}/{frequency}: {_describe_exception(cause)}")


class YahooStatementsUnavailableError(RuntimeError):
    """Statements could not be obtained in usable form: outage or schema break.

    Deliberately not a ``LookupError``. ``LookupError`` means "this issuer has
    no financial statements" and becomes HTTP 404; this error means "Yahoo did
    not deliver usable statements" and must surface as an upstream failure
    (HTTP 502) instead of an issuer that looks statement-less.
    """

    def __init__(
        self,
        symbol: str,
        *,
        failures: Sequence[StatementFrameFailure],
        total_frames: int,
        empty_frames: int,
        schema_frames: int,
        unmapped_frames: int,
    ) -> None:
        self.symbol = symbol
        self.failures = tuple(failures)
        self.total_frames = total_frames
        self.empty_frames = empty_frames
        self.schema_frames = schema_frames
        self.unmapped_frames = unmapped_frames
        reasons = "; ".join(item.describe() for item in self.failures[:4])
        counts = (
            f"{len(self.failures)}/{total_frames} raised"
            f", {schema_frames}/{total_frames} had no period columns"
            f", {unmapped_frames}/{total_frames} had no recognized line item"
            f", {empty_frames}/{total_frames} were empty"
        )
        detail = f" ({reasons})" if reasons else ""
        super().__init__(f"Yahoo statements unavailable for {symbol}: {counts}{detail}")


class YahooFundamentalsProvider:
    """Personal-mode financial statements for non-SEC issuers.

    This path is intentionally isolated from public mode. Yahoo provides statement
    period ends and frequency rather than SEC tagged contexts, so approximate period
    starts are used only for ratio/grouping math and disclosed in provenance.
    """

    descriptor = ProviderDescriptor(
        name="yahoo-fundamentals",
        license_class=LicenseClass.PERSONAL_ONLY,
        redistributable=False,
        description=(
            "Yahoo Finance normalized financial statements via yfinance; personal use only."
        ),
    )

    ROW_ALIASES: ClassVar[dict[str, tuple[str, ...]]] = {
        "revenue": ("Total Revenue", "Operating Revenue"),
        "gross_profit": ("Gross Profit",),
        "operating_income": ("Operating Income",),
        "net_income": ("Net Income Common Stockholders", "Net Income"),
        "eps_diluted": ("Diluted EPS",),
        "shares_diluted": ("Diluted Average Shares",),
        "cash": (
            "Cash Cash Equivalents And Short Term Investments",
            "Cash And Cash Equivalents",
            "Cash Financial",
        ),
        "current_assets": ("Current Assets",),
        "current_liabilities": ("Current Liabilities",),
        "assets": ("Total Assets",),
        "liabilities": (
            "Total Liabilities Net Minority Interest",
            "Total Liabilities",
        ),
        "equity": (
            "Stockholders Equity",
            "Total Equity Gross Minority Interest",
            "Common Stock Equity",
        ),
        "operating_cash_flow": (
            "Operating Cash Flow",
            "Total Cash From Operating Activities",
        ),
        "capex": ("Capital Expenditure", "Capital Expenditures"),
    }

    LABELS: ClassVar[dict[str, str]] = {
        "revenue": "Revenue",
        "gross_profit": "Gross profit",
        "operating_income": "Operating income",
        "net_income": "Net income",
        "eps_diluted": "Diluted EPS",
        "shares_diluted": "Diluted shares",
        "cash": "Cash and cash equivalents",
        "current_assets": "Current assets",
        "current_liabilities": "Current liabilities",
        "assets": "Total assets",
        "liabilities": "Total liabilities",
        "equity": "Equity",
        "operating_cash_flow": "Operating cash flow",
        "capex": "Capital expenditure",
    }

    CURRENCY_SUFFIX_HINTS: ClassVar[dict[str, str]] = {
        ".T": "JPY",
    }

    STATEMENT_LOADERS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("income_statement", "get_income_stmt"),
        ("balance_sheet", "get_balance_sheet"),
        ("cash_flow", "get_cash_flow"),
    )

    def __init__(self, settings: Settings) -> None:
        enforce_provider_policy(
            self.descriptor,
            mode=settings.mode,
            allow_personal_in_public=settings.allow_personal_provider_in_public,
        )
        self.settings = settings

    def company_facts(self, symbol: str) -> Fundamentals:
        normalized = normalize_symbol(symbol)
        ticker = yf.Ticker(normalized)
        metadata = self._safe_history_metadata(ticker)
        currency = self._statement_currency(
            metadata.get("currency") or self._currency_hint(normalized)
        )
        company_name = str(
            metadata.get("longName")
            or metadata.get("shortName")
            or metadata.get("symbol")
            or normalized
        )

        metrics: dict[str, MetricSeries] = {}
        sample = self._statement_frames(ticker)
        for frequency, statements in sample.frames.items():
            for frame in statements:
                if self._merge_frame(metrics, frame, frequency, currency) == 0:
                    # A frame that loaded but matched no known row label is the
                    # row-level twin of a missing column: Yahoo changed shape.
                    sample.unmapped_frames += 1

        for series in metrics.values():
            dedup: dict[tuple[date, str | None, Decimal], MetricPoint] = {}
            for point in series.points:
                dedup[(point.period_end, point.fiscal_period, point.value)] = point
            series.points = sorted(dedup.values(), key=lambda item: item.period_end)

        self._fill_fiscal_years(metrics, self._yearly_period_ends(sample.frames))
        self._derive_eps_diluted(metrics, currency)

        if not metrics:
            if sample.failures or sample.schema_frames or sample.unmapped_frames:
                raise YahooStatementsUnavailableError(
                    normalized,
                    failures=sample.failures,
                    total_frames=sample.total_frames,
                    empty_frames=sample.empty_frames,
                    schema_frames=sample.schema_frames,
                    unmapped_frames=sample.unmapped_frames,
                )
            raise LookupError(f"No Yahoo financial statements available for {normalized}")

        now = datetime.now(UTC)
        return Fundamentals(
            symbol=normalized,
            cik="",
            company_name=company_name,
            metrics=metrics,
            provenance=Provenance(
                provider="yahoo/yfinance",
                source="Yahoo Finance financial statements",
                source_url=f"https://finance.yahoo.com/quote/{normalized}/financials/",
                license_class=LicenseClass.PERSONAL_ONLY,
                retrieved_at=now,
                as_of=max(
                    (point.period_end for series in metrics.values() for point in series.points),
                    default=None,
                ),
                notes=[
                    (
                        "Personal-mode financial statements for issuers outside the SEC "
                        "company-facts universe, including Japanese listings."
                    ),
                    (
                        "Statement period ends and annual/quarterly frequency come from Yahoo; "
                        "approximate starts are inferred only for normalized ratio/grouping math."
                    ),
                    (
                        "Company identity and currency use Yahoo price-history metadata when "
                        "available; metadata failure does not invalidate otherwise usable "
                        "statements."
                    ),
                    *self._degradation_notes(sample),
                    (
                        "Capital expenditure is normalized to a positive cash outflow before "
                        "FCF calculations."
                    ),
                    (
                        "Diluted EPS is derived as net income / diluted shares for periods "
                        "where Yahoo reports no usable EPS row."
                    ),
                    "Do not redistribute this normalized Yahoo dataset from public mode.",
                ],
            ),
        )

    @classmethod
    def _currency_hint(cls, symbol: str) -> str:
        return next(
            (
                currency
                for suffix, currency in cls.CURRENCY_SUFFIX_HINTS.items()
                if symbol.endswith(suffix)
            ),
            "",
        )

    @staticmethod
    def _statement_currency(raw: object) -> str:
        """Statement currency as an ISO-style code, or ``""`` when unusable.

        Yahoo's metadata value is accepted only after the same validation the
        quote path applies (``normalize_currency``): stripped, upper-cased and
        three letters long. A blank, malformed or non-ISO value degrades to an
        empty unit instead of becoming a metric unit, so a bad metadata field
        can never silently mislabel every statement amount (F4).
        """

        if raw is None:
            return ""
        text = str(raw).strip().upper()
        if not text:
            return ""
        try:
            return normalize_currency(text)
        except InputValidationError:
            return ""

    @staticmethod
    def _safe_history_metadata(ticker: yf.Ticker) -> dict[str, Any]:
        try:
            value = ticker.get_history_metadata()
        except Exception:
            return {}
        # yfinance 1.7 returns a ``HistoryMetadata`` Mapping, not a dict; testing
        # ``isinstance(value, dict)`` discarded a perfectly good payload, which
        # silently degraded company_name to the symbol and the statement
        # currency to a suffix guess (P5 R4).
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _load_frame(
        loader: Any,
        frequency: str,
        statement: str,
    ) -> pd.DataFrame:
        """Load one statement frame or raise with the reason it could not be read.

        Older yfinance releases accepted ``frequency=`` instead of ``freq=``, so a
        ``TypeError``/``ValueError`` retries the other keyword. A retry that also
        fails, a non-``DataFrame`` payload, or any other exception is raised as
        :class:`YahooStatementFetchError`: an outage never becomes an empty frame
        that later reads as "this issuer has no statements"
        (docs/SILENT_FAILURE_AUDIT_20261023.md §3 #6/#7).
        """

        try:
            frame = loader(freq=frequency)
        except (TypeError, ValueError):
            try:
                frame = loader(frequency=frequency)
            except Exception as exc:
                raise YahooStatementFetchError(statement, frequency, exc) from exc
        except Exception as exc:
            raise YahooStatementFetchError(statement, frequency, exc) from exc
        if not isinstance(frame, pd.DataFrame):
            raise YahooStatementFetchError(
                statement,
                frequency,
                TypeError(f"yfinance returned {type(frame).__name__}, not a DataFrame"),
            )
        return frame

    @staticmethod
    def _frame_state(frame: pd.DataFrame) -> Literal["ok", "empty", "schema"]:
        """Classify a loaded frame as usable, genuinely empty, or a shape break.

        An empty frame means Yahoo reported no statement for that period: no data.
        A frame with rows but no period columns (or columns but no rows) is a
        format change we cannot read, and must not be reported as "no financials".
        """

        has_index = len(frame.index) > 0
        has_columns = len(frame.columns) > 0
        if has_index and has_columns:
            return "ok"
        if not has_index and not has_columns:
            return "empty"
        return "schema"

    def _statement_frames(self, ticker: yf.Ticker) -> _StatementSample:
        """Load every statement frame, keeping failures apart from empty frames."""

        sample = _StatementSample()
        for frequency in ("yearly", "quarterly"):
            for statement, attribute in self.STATEMENT_LOADERS:
                sample.attempted += 1
                loader = getattr(ticker, attribute, None)
                if loader is None:
                    sample.failures.append(
                        StatementFrameFailure(
                            statement,
                            frequency,
                            f"AttributeError: yfinance ticker has no {attribute}",
                        )
                    )
                    continue
                try:
                    frame = self._load_frame(loader, frequency, statement)
                except YahooStatementFetchError as exc:
                    sample.failures.append(
                        StatementFrameFailure(
                            statement,
                            frequency,
                            _describe_exception(exc.cause),
                        )
                    )
                    continue
                state = self._frame_state(frame)
                if state == "empty":
                    sample.empty_frames += 1
                    continue
                if state == "schema":
                    sample.schema_frames += 1
                    continue
                sample.frames[frequency].append(frame)
        return sample

    @classmethod
    def _merge_frame(
        cls,
        metrics: dict[str, MetricSeries],
        frame: pd.DataFrame,
        frequency: str,
        currency: str,
    ) -> int:
        """Merge one statement frame and return how many row aliases it matched.

        A zero return means nothing in the frame was recognized: the caller keeps
        that count so a Yahoo row-label change cannot pass as "no data".
        """

        if frame.empty:
            return 0
        normalized_index = {cls._normalize_row_name(item): item for item in frame.index}
        matched = 0
        for key, aliases in cls.ROW_ALIASES.items():
            source_row = next(
                (
                    normalized_index[cls._normalize_row_name(alias)]
                    for alias in aliases
                    if cls._normalize_row_name(alias) in normalized_index
                ),
                None,
            )
            if source_row is None:
                continue
            matched += 1
            row = frame.loc[source_row]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            for raw_period, raw_value in row.items():
                point = cls._point(key, raw_period, raw_value, frequency, currency)
                if point is None:
                    continue
                series = metrics.setdefault(
                    key,
                    MetricSeries(key=key, label=cls.LABELS.get(key, key), points=[]),
                )
                series.points.append(point)
        return matched

    @staticmethod
    def _degradation_notes(sample: _StatementSample) -> list[str]:
        """Provenance lines for a partially usable statement load.

        Returned through the existing ``Provenance.notes`` field, so an operator
        or agent can see how many frames were missing and why without any new
        response key. Frames Yahoo returned empty are not listed: an empty frame
        is Yahoo reporting no statement, which is plain absence of data.
        """

        notes: list[str] = []
        total = sample.total_frames
        if sample.failures:
            reasons = "; ".join(item.describe() for item in sample.failures)
            notes.append(
                f"{len(sample.failures)} of {total} Yahoo statement frames could not be "
                f"retrieved ({reasons}); metrics cover only the frames that loaded, so a "
                "missing metric is an upstream failure rather than a reported zero."
            )
        if sample.schema_frames:
            notes.append(
                f"{sample.schema_frames} of {total} Yahoo statement frames had no period "
                "columns and were not used (possible Yahoo statement-format change)."
            )
        if sample.unmapped_frames:
            notes.append(
                f"{sample.unmapped_frames} of {total} Yahoo statement frames contained no "
                "recognized line item and contributed no metrics (possible Yahoo row-label "
                "change)."
            )
        return notes

    @staticmethod
    def _yearly_period_ends(frames: dict[str, list[pd.DataFrame]]) -> list[date]:
        """Collect every period end across all yearly-frequency statement frames."""

        ends: list[date] = []
        for frame in frames.get("yearly", []):
            for column in frame.columns:
                try:
                    timestamp = pd.Timestamp(str(column))
                except (TypeError, ValueError, OverflowError):
                    continue
                if pd.isna(timestamp):
                    continue
                ends.append(timestamp.date())
        return ends

    @classmethod
    def _fill_fiscal_years(
        cls,
        metrics: dict[str, MetricSeries],
        yearly_period_ends: list[date],
    ) -> None:
        """Assign fiscal years to every point from the inferred fiscal year-end month.

        The fiscal year-end month is the most common month among yearly statement
        period ends (needs at least two). A period ending in the fiscal-end month
        closes that fiscal year; a period ending in a later month belongs to the
        next fiscal year; a period ending before it belongs to the current one.
        Without enough yearly frames, only FY points can be safely labeled with
        their period-end year and quarterly points stay unlabeled.
        """

        fiscal_month: int | None = None
        if len(yearly_period_ends) >= 2:
            month_counts = Counter(period.month for period in yearly_period_ends)
            fiscal_month = max(month_counts, key=lambda month: (month_counts[month], -month))
        for series in metrics.values():
            for point in series.points:
                point.fiscal_year = cls._fiscal_year_for(point, fiscal_month)

    @staticmethod
    def _fiscal_year_for(point: MetricPoint, fiscal_month: int | None) -> int | None:
        if fiscal_month is None:
            return point.period_end.year if point.fiscal_period == "FY" else None
        month = point.period_end.month
        if month == fiscal_month:
            return point.period_end.year
        if month > fiscal_month:
            return point.period_end.year + 1
        return point.period_end.year

    @classmethod
    def _derive_eps_diluted(cls, metrics: dict[str, MetricSeries], currency: str) -> None:
        """Derive diluted EPS as net income / diluted shares when Yahoo has no usable EPS.

        Fails closed per period: division is refused unless both inputs exist and
        diluted shares are non-zero. A non-zero Yahoo-provided EPS value is never
        overwritten; a zero-valued one is replaced.
        """

        net_income = metrics.get("net_income")
        shares = metrics.get("shares_diluted")
        if net_income is None or shares is None:
            return
        net_by_period: dict[tuple[date, str | None], MetricPoint] = {
            (point.period_end, point.fiscal_period): point for point in net_income.points
        }
        shares_by_period: dict[tuple[date, str | None], MetricPoint] = {
            (point.period_end, point.fiscal_period): point for point in shares.points
        }
        eps_series = metrics.get("eps_diluted")
        eps_by_period: dict[tuple[date, str | None], MetricPoint] = {
            (point.period_end, point.fiscal_period): point
            for point in (eps_series.points if eps_series is not None else [])
        }
        unit = f"{currency}/share" if currency else "per share"
        derived: list[MetricPoint] = []
        for period_key, net_point in net_by_period.items():
            share_point = shares_by_period.get(period_key)
            if share_point is None:
                continue
            shares_value = share_point.value
            if not shares_value.is_finite() or shares_value == 0:
                continue
            if not net_point.value.is_finite():
                continue
            existing = eps_by_period.get(period_key)
            if existing is not None and existing.value != Decimal("0"):
                continue
            value = (net_point.value / shares_value).quantize(
                Decimal("0.000001"),
                rounding=ROUND_HALF_UP,
            )
            derived.append(
                MetricPoint(
                    period_start=net_point.period_start,
                    period_end=net_point.period_end,
                    fiscal_year=net_point.fiscal_year,
                    fiscal_period=net_point.fiscal_period,
                    value=value,
                    unit=unit,
                    accession=net_point.accession,
                    filed=net_point.filed,
                    form="Yahoo normalized statement",
                )
            )
        if not derived:
            return
        series = metrics.setdefault(
            "eps_diluted",
            MetricSeries(
                key="eps_diluted",
                label=cls.LABELS.get("eps_diluted", "eps_diluted"),
                points=[],
            ),
        )
        derived_keys = {(point.period_end, point.fiscal_period) for point in derived}
        series.points = [
            point
            for point in series.points
            if point.value != Decimal("0")
            or (point.period_end, point.fiscal_period) not in derived_keys
        ]
        series.points.extend(derived)
        series.points.sort(key=lambda point: point.period_end)

    @staticmethod
    def _normalize_row_name(value: object) -> str:
        return "".join(ch.lower() for ch in str(value) if ch.isalnum())

    @classmethod
    def _point(
        cls,
        key: str,
        raw_period: object,
        raw_value: object,
        frequency: str,
        currency: str,
    ) -> MetricPoint | None:
        try:
            number = float(str(raw_value))
        except ValueError:
            return None
        if not math.isfinite(number):
            return None
        if key == "capex":
            number = abs(number)
        try:
            timestamp = pd.Timestamp(str(raw_period))
        except (TypeError, ValueError, OverflowError):
            return None
        if pd.isna(timestamp):
            return None
        period = timestamp.date()

        annual = frequency == "yearly"
        period_start = period - timedelta(days=364 if annual else 89)
        fiscal_period = "FY" if annual else "Q"
        unit = "shares" if key == "shares_diluted" else currency
        if key == "eps_diluted":
            unit = f"{currency}/share" if currency else "per share"
        return MetricPoint(
            period_start=period_start,
            period_end=period,
            fiscal_year=None,
            fiscal_period=fiscal_period,
            value=Decimal(str(number)),
            unit=unit,
            # ``currency`` stays None on purpose: the provider gives the
            # listing's *price* currency here, not the statement's reporting
            # currency (Yahoo's ``financialCurrency``), and stamping the former
            # would let the valuation currency gate compare the wrong thing.
            # Follow-up recorded in docs/P5_R4_QUOTE_CURRENCY_20261023.md.
            accession=None,
            filed=None,
            form="Yahoo normalized statement",
        )
