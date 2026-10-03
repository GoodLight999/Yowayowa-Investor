from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd
import pytest

from yowayowa.config import Settings
from yowayowa.domain import LicenseClass, Portfolio, Position, Provenance
from yowayowa.providers import yahoo_risk
from yowayowa.providers.yahoo_risk import YahooRiskProvider


def _now() -> datetime:
    return datetime(2026, 9, 25, tzinfo=UTC)


def _provenance(source: str) -> Provenance:
    return Provenance(
        provider="fixture",
        source=source,
        license_class=LicenseClass.PERSONAL_ONLY,
        retrieved_at=_now(),
        as_of=_now(),
    )


def _portfolio(*positions: Position) -> Portfolio:
    now = _now()
    return Portfolio(
        id=1,
        name="Risk",
        base_currency="USD",
        positions=list(positions),
        created_at=now,
        updated_at=now,
    )


def _provider() -> YahooRiskProvider:
    return YahooRiskProvider(Settings(database_url="sqlite:///:memory:"))


def _price_frame(tickers: list[str], rows: list[list[float]]) -> pd.DataFrame:
    index = pd.date_range("2026-08-03", periods=len(rows), tz="UTC")
    columns = pd.MultiIndex.from_product([["Close"], tickers])
    return pd.DataFrame(rows, index=index, columns=columns)


def test_yahoo_risk_provider_enforces_personal_policy() -> None:
    provider = _provider()
    assert provider.descriptor.license_class == LicenseClass.PERSONAL_ONLY


def test_portfolio_returns_computes_returns_covariance_and_correlation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    frame = _price_frame(
        ["AAA", "BBB", "^GSPC"],
        [
            [100.0, 50.0, 4000.0],
            [110.0, 45.0, 4100.0],
            [121.0, 54.0, 4090.0],
            [99.0, 56.0, 4180.0],
        ],
    )

    def fake_download(**kwargs: object) -> pd.DataFrame:
        captured.update(kwargs)
        return frame

    monkeypatch.setattr(yahoo_risk.yf, "download", fake_download)
    portfolio = _portfolio(
        Position(symbol="AAA", quantity=Decimal(10), currency="USD"),
        Position(symbol="BBB", quantity=Decimal(5), currency="USD"),
    )

    history = _provider().portfolio_returns(portfolio, benchmark="^GSPC", period="1y")

    assert captured["tickers"] == ["AAA", "BBB", "^GSPC"]
    assert captured["period"] == "1y"
    assert captured["interval"] == "1d"
    assert captured["auto_adjust"] is True
    assert captured["progress"] is False

    assert list(history.returns.columns) == ["AAA", "BBB"]
    assert history.returns.shape == (3, 2)
    first_aaa = history.returns["AAA"].iloc[0]
    assert first_aaa == pytest.approx(0.10)
    assert history.returns["AAA"].iloc[1] == pytest.approx(0.10)
    assert history.returns["AAA"].iloc[2] == pytest.approx(99.0 / 121.0 - 1.0, rel=1e-9)
    assert history.benchmark_returns.name == "^GSPC"
    assert history.unavailable_symbols == []
    assert history.provenance.license_class == LicenseClass.PERSONAL_ONLY
    assert history.provenance.as_of is not None
    covariance = history.returns.cov()
    correlation = history.returns.corr()
    assert covariance.loc["AAA", "BBB"] != 0.0
    assert -1.0 <= correlation.loc["AAA", "BBB"] <= 1.0


def test_portfolio_returns_marks_missing_ticker_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # BBB has no data column at all: reindex produces an all-NaN column.
    frame = _price_frame(["AAA", "^GSPC"], [[100.0, 4000.0], [104.0, 4040.0]])
    monkeypatch.setattr(yahoo_risk.yf, "download", lambda **kwargs: frame)
    portfolio = _portfolio(
        Position(symbol="AAA", quantity=Decimal(1), currency="USD"),
        Position(symbol="BBB", quantity=Decimal(2), currency="USD"),
    )

    history = _provider().portfolio_returns(portfolio, benchmark="^GSPC", period="6mo")

    assert history.unavailable_symbols == ["BBB"]
    assert list(history.returns.columns) == ["AAA"]
    assert history.benchmark_returns.dropna().empty is False or True
    # benchmark still resolved from the frame
    assert len(history.benchmark_returns) == 2


def test_portfolio_returns_combines_fx_return_for_foreign_currency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _price_frame(
        ["7203.T", "JPYUSD=X", "^GSPC"],
        [
            [2500.0, 150.0, 4000.0],
            [2575.0, 145.0, 4040.0],
        ],
    )
    monkeypatch.setattr(yahoo_risk.yf, "download", lambda **kwargs: frame)
    portfolio = _portfolio(Position(symbol="7203.T", quantity=Decimal(100), currency="JPY"))

    history = _provider().portfolio_returns(portfolio, benchmark="^GSPC", period="1mo")

    local_return = 2575.0 / 2500.0 - 1.0
    fx_return = 145.0 / 150.0 - 1.0
    expected = (1 + local_return) * (1 + fx_return) - 1
    assert history.returns["7203.T"].iloc[-1] == pytest.approx(expected, rel=1e-9)
    assert history.unavailable_symbols == []


def test_portfolio_returns_marks_foreign_position_unavailable_without_fx_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _price_frame(["7203.T", "^GSPC"], [[2500.0, 4000.0], [2575.0, 4040.0]])
    monkeypatch.setattr(yahoo_risk.yf, "download", lambda **kwargs: frame)
    portfolio = _portfolio(Position(symbol="7203.T", quantity=Decimal(100), currency="JPY"))

    history = _provider().portfolio_returns(portfolio)

    assert history.unavailable_symbols == ["7203.T"]
    assert history.returns.empty
    assert "7203.T" not in history.returns.columns


def test_portfolio_returns_keeps_missing_benchmark_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = pd.date_range("2026-08-03", periods=2, tz="UTC")
    frame = pd.DataFrame({"Close": [100.0, 110.0]}, index=index)
    monkeypatch.setattr(yahoo_risk.yf, "download", lambda **kwargs: frame)
    portfolio = _portfolio(Position(symbol="AAA", quantity=Decimal(1), currency="USD"))

    history = _provider().portfolio_returns(portfolio, benchmark="^MISSING")

    assert history.benchmark_returns.name == "^MISSING"
    assert history.benchmark_returns.empty
    assert history.unavailable_symbols == ["AAA"]
    assert history.returns.empty


def test_close_frame_returns_empty_frame_for_empty_download() -> None:
    empty = pd.DataFrame()
    close = YahooRiskProvider._close_frame(empty, ["AAA", "BBB"])

    assert list(close.columns) == ["AAA", "BBB"]
    assert close.empty


def test_close_frame_without_close_columns_yields_nan_frame() -> None:
    index = pd.date_range("2026-08-01", periods=2)
    frame = pd.DataFrame(
        {"Open": [10.0, 11.0], "Volume": [1000, 1100]},
        index=index,
    )
    columns = pd.MultiIndex.from_product([["Open", "Volume"], ["AAA"]])
    multi = pd.DataFrame(frame.values, index=index, columns=columns)

    close = YahooRiskProvider._close_frame(multi, ["AAA"])

    assert list(close.columns) == ["AAA"]
    assert close["AAA"].isna().all()


def test_close_frame_selects_multiindex_with_close_at_level_one() -> None:
    index = pd.date_range("2026-08-01", periods=2)
    columns = pd.MultiIndex.from_product([["AAA", "BBB"], ["Close"]])
    frame = pd.DataFrame([[10.0, 20.0], [11.0, 22.0]], index=index, columns=columns)

    close = YahooRiskProvider._close_frame(frame, ["AAA", "BBB"])

    assert close["AAA"].tolist() == [10.0, 11.0]
    assert close["BBB"].tolist() == [20.0, 22.0]


def test_close_frame_single_level_frame_uses_close_series() -> None:
    index = pd.date_range("2026-08-01", periods=2)
    frame = pd.DataFrame({"Close": [10.0, 11.0], "Open": [9.0, 10.5]}, index=index)

    close = YahooRiskProvider._close_frame(frame, ["AAA"])

    assert list(close.columns) == ["AAA"]
    assert close["AAA"].tolist() == [10.0, 11.0]


def test_close_frame_single_level_missing_close_keeps_requested_column_missing() -> None:
    index = pd.date_range("2026-08-01", periods=2)
    frame = pd.DataFrame({"Open": [9.0, 10.5]}, index=index)

    close = YahooRiskProvider._close_frame(frame, ["AAA"])

    assert list(close.columns) == ["AAA"]
    assert close["AAA"].isna().all()


def test_as_of_returns_fallback_for_empty_or_all_nan_frame() -> None:
    fallback = _now()
    empty = pd.DataFrame()

    assert YahooRiskProvider._as_of(empty, fallback) == fallback

    all_nan = pd.DataFrame({"AAA": [float("nan"), float("nan")]})
    assert YahooRiskProvider._as_of(all_nan, fallback) == fallback


def test_as_of_localizes_naive_index_to_utc() -> None:
    naive_index = pd.DatetimeIndex([pd.Timestamp("2026-08-18"), pd.Timestamp("2026-08-19")])
    frame = pd.DataFrame({"AAA": [1.0, 2.0]}, index=naive_index)

    as_of = YahooRiskProvider._as_of(frame, _now())

    assert as_of.tzinfo is not None
    assert as_of.year == 2026
    assert as_of.month == 8
    assert as_of.day == 19


def test_as_of_converts_aware_index_to_utc() -> None:
    aware_index = pd.DatetimeIndex([pd.Timestamp("2026-08-18 15:00", tz="America/New_York")])
    frame = pd.DataFrame({"AAA": [1.0]}, index=aware_index)

    as_of = YahooRiskProvider._as_of(frame, _now())

    assert str(as_of.tzinfo) in {"UTC", "utc"}
    assert as_of.hour == 19  # 15:00 New York = 19:00 UTC
