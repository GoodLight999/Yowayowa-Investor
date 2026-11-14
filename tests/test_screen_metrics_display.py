"""CLI display contract for ``yowayowa screen`` (P5 R11, 2026-10-23).

``screen`` used to print the entire metric dictionary as one JSON cell, so the
operator could not read a metric name next to its value and a narrow terminal
ellipsized the payload. The command now prints two tables: the per-symbol
summary (Symbol/Matched/Failures) and one long metrics table
(Symbol/Metric/Value) with one row per metric, keys in sorted order and every
API metric listed.

Only display changed: the HTTP request payload, the response row/symbol order,
the meaning of Matched/Failures, the arithmetic and the exit behaviour are the
same as before. Values are echoed unchanged -- ``None`` is missing data and
renders as ``unavailable``, a real ``0`` keeps its own representation, and no
value is rescaled to %, multiples or JPY.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from yowayowa.cli_entry import app as entry_app

runner = CliRunner()


class _StubResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def raise_for_status(self) -> _StubResponse:
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://api.test/v1/screen")
            response = httpx.Response(self.status_code, request=request, text=self.text)
            raise httpx.HTTPStatusError("boom", request=request, response=response)
        return self

    def json(self) -> Any:
        return self._payload


class _StubClient:
    def __init__(
        self,
        payload: Any,
        calls: list[dict[str, Any]],
        status_code: int = 200,
    ) -> None:
        self._payload = payload
        self._calls = calls
        self._status_code = status_code

    def __enter__(self) -> _StubClient:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def post(self, path: str, json: Any = None) -> _StubResponse:
        self._calls.append({"path": path, "json": json})
        return _StubResponse(self._payload, self._status_code)


def _invoke(
    monkeypatch: pytest.MonkeyPatch,
    payload: Any,
    *,
    args: list[str] | None = None,
    cols: str = "80",
    status_code: int = 200,
) -> tuple[Any, list[dict[str, Any]]]:
    import yowayowa.cli as cli_module

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        cli_module,
        "_client",
        lambda base_url, token: _StubClient(payload, calls, status_code),
    )
    result = runner.invoke(
        entry_app,
        ["screen", *(args if args is not None else ["7203.T", "6758.T"])],
        env={"COLUMNS": cols, "TERM": "dumb", "NO_COLOR": "1"},
    )
    return result, calls


def _metrics_block(output: str) -> str:
    """Return the last rendered table (the metrics table when rows exist)."""

    return output.split("┏")[-1]


def _cells(block: str) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for line in block.splitlines():
        if not line.startswith("│"):
            continue
        parts = [cell.strip() for cell in line.strip("│").split("│")]
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def _metric_cells(output: str) -> dict[tuple[str, str], str]:
    return {(symbol, metric): value for symbol, metric, value in _cells(_metrics_block(output))}


def _folded_metric_rows(block: str) -> list[tuple[str, str, str]]:
    """Reassemble metric rows whose cells rich folded over several lines."""

    rows: list[list[str]] = []
    for symbol, metric, value in _cells(block):
        if symbol:
            rows.append([symbol, metric, value])
        elif rows:
            rows[-1][1] += metric
            rows[-1][2] += value
    return [(symbol, metric, value) for symbol, metric, value in rows]


def _summary_cells(output: str) -> list[tuple[str, str, str]]:
    return _cells(output.split("┏")[1])


def _row(
    symbol: str,
    metrics: dict[str, Any],
    *,
    matched: bool = True,
    failures: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "metrics": metrics,
        "matched": matched,
        "failures": failures or [],
    }


def _payload(*rows: dict[str, Any]) -> dict[str, Any]:
    return {"rows": list(rows), "evaluated_at": "2026-10-23T12:34:56.789012Z"}


def test_every_metric_is_one_row_with_keys_in_sorted_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two symbols with different key sets: one row each, sorted, none dropped."""

    first = {
        "revenue": 45095325000000.0,
        "operating_margin": 0.118,
        "cash": 0.0,
        "working_capital": 10000000000000.0,
    }
    second = {"revenue": 13000000000000.0, "net_margin": 0.0746, "eps_diluted": 780.4}
    result, _calls = _invoke(monkeypatch, _payload(_row("7203.T", first), _row("6758.T", second)))

    assert result.exit_code == 0, result.output
    cells = _metric_cells(result.output)
    assert cells == {
        ("7203.T", "cash"): "0.0",
        ("7203.T", "operating_margin"): "0.118",
        ("7203.T", "revenue"): "45095325000000.0",
        ("7203.T", "working_capital"): "10000000000000.0",
        ("6758.T", "eps_diluted"): "780.4",
        ("6758.T", "net_margin"): "0.0746",
        ("6758.T", "revenue"): "13000000000000.0",
    }
    # Metric order inside a symbol block is the sorted key order, and symbol
    # blocks keep the API's row order (7203.T before 6758.T).
    ordered = [metric for symbol, metric, _value in _cells(_metrics_block(result.output))]
    assert ordered == [
        "cash",
        "operating_margin",
        "revenue",
        "working_capital",
        "eps_diluted",
        "net_margin",
        "revenue",
    ]
    assert [symbol for symbol, _metric, _value in _cells(_metrics_block(result.output))] == [
        "7203.T",
        "7203.T",
        "7203.T",
        "7203.T",
        "6758.T",
        "6758.T",
        "6758.T",
    ]


def test_none_zero_negative_and_decimal_stay_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing data is not zero: ``None`` -> unavailable, ``0`` -> its own value."""

    metrics = {
        "gross_profit": None,
        "cash": 0.0,
        "operating_cash_flow": -1234567890.5,
        "eps_diluted": 372.13,
        "free_cash_flow": 1450000000000.0,
        "capex": -350000000000.0,
    }
    result, _calls = _invoke(monkeypatch, _payload(_row("7203.T", metrics)))

    assert result.exit_code == 0, result.output
    cells = _metric_cells(result.output)
    assert cells[("7203.T", "gross_profit")] == "unavailable"
    assert cells[("7203.T", "cash")] == "0.0"
    assert cells[("7203.T", "operating_cash_flow")] == "-1234567890.5"
    assert cells[("7203.T", "eps_diluted")] == "372.13"
    assert cells[("7203.T", "free_cash_flow")] == "1450000000000.0"
    assert cells[("7203.T", "capex")] == "-350000000000.0"
    # Exactly one missing value: no zero/EMPTY fill, no fabricated metrics.
    assert list(cells.values()).count("unavailable") == 1
    assert list(cells.values()).count("0.0") == 1
    assert len(cells) == len(metrics)


def test_integer_scalars_are_echoed_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A JSON integer zero stays ``0`` -- still distinct from missing data."""

    metrics = {"cash": 0, "capex": -5, "revenue": 0.0, "gross_profit": None}
    result, _calls = _invoke(monkeypatch, _payload(_row("7203.T", metrics)))

    cells = _metric_cells(result.output)
    assert cells[("7203.T", "cash")] == "0"
    assert cells[("7203.T", "capex")] == "-5"
    assert cells[("7203.T", "revenue")] == "0.0"
    assert cells[("7203.T", "gross_profit")] == "unavailable"


def test_no_unit_inference_or_rescaling(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ratio stays a ratio: nothing is multiplied into % or divided into 倍."""

    metrics = {"operating_margin": 0.118, "liabilities_to_equity": 1.784}
    result, _calls = _invoke(monkeypatch, _payload(_row("7203.T", metrics)))

    cells = _metric_cells(result.output)
    assert cells[("7203.T", "operating_margin")] == "0.118"
    assert cells[("7203.T", "liabilities_to_equity")] == "1.784"
    assert "11.8" not in result.output
    assert "%" not in result.output
    assert "JPY" not in result.output


def test_long_key_folds_instead_of_being_ellipsized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key wider than the terminal folds; no character is replaced by ``…``."""

    long_key = "operating_cash_flow_growth_yoy_normalised_for_split_adjusted_filings"
    result, _calls = _invoke(
        monkeypatch,
        _payload(_row("7203.T", {long_key: None})),
        cols="80",
    )

    assert result.exit_code == 0, result.output
    assert "…" not in result.output
    block = _metrics_block(result.output)
    assert max(len(line) for line in block.splitlines()) <= 80
    # The folded cells reassemble into exactly one metric row: the key and the
    # value survive intact instead of being replaced by an ellipsis.
    assert _folded_metric_rows(block) == [("7203.T", long_key, "unavailable")]


def test_all_metrics_of_the_payload_are_listed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """36 metrics x 2 symbols = 72 metric rows: none omitted, none added."""

    keys = [
        "assets",
        "capex",
        "capex_to_revenue",
        "cash",
        "cash_ratio",
        "cash_to_assets",
        "current_assets",
        "current_liabilities",
        "current_ratio",
        "eps_diluted",
        "eps_growth_yoy",
        "equity",
        "equity_to_assets",
        "free_cash_flow",
        "free_cash_flow_margin",
        "gross_margin",
        "gross_profit",
        "gross_profit_growth_yoy",
        "liabilities",
        "liabilities_to_assets",
        "liabilities_to_equity",
        "net_income",
        "net_income_growth_yoy",
        "net_margin",
        "operating_cash_flow",
        "operating_cash_flow_growth_yoy",
        "operating_cash_flow_margin",
        "operating_income",
        "operating_income_growth_yoy",
        "operating_margin",
        "return_on_assets",
        "return_on_equity",
        "revenue",
        "revenue_growth_yoy",
        "working_capital",
    ]
    rows = [_row(symbol, {key: 1.5 for key in keys}) for symbol in ("7203.T", "6758.T")]
    result, _calls = _invoke(monkeypatch, _payload(*rows))

    cells = _metric_cells(result.output)
    assert len(cells) == 2 * len(keys)
    assert {metric for _symbol, metric in cells} == set(keys)


def test_matched_and_failures_keep_their_meaning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The summary keeps Symbol/Matched/Failures; the JSON blob does not return."""

    result, _calls = _invoke(
        monkeypatch,
        _payload(
            _row("7203.T", {"operating_margin": 0.0923}, matched=False, failures=["net_margin"]),
            _row("6758.T", {"operating_margin": 0.118}, matched=True),
        ),
    )

    assert result.exit_code == 0, result.output
    assert _summary_cells(result.output) == [
        ("7203.T", "no", "net_margin"),
        ("6758.T", "yes", ""),
    ]
    assert "Metrics" not in result.output  # the old single-cell column is gone
    assert "{" not in result.output and "}" not in result.output
    assert "null" not in result.output


def test_empty_metrics_dict_is_announced_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A symbol whose metrics dict is empty says so instead of showing nothing."""

    result, _calls = _invoke(
        monkeypatch,
        _payload(_row("7203.T", {}), _row("6758.T", {"revenue": 13000000000000.0})),
    )

    assert result.exit_code == 0, result.output
    assert "Unavailable metrics: 7203.T" in result.output
    cells = _metric_cells(result.output)
    assert cells == {("6758.T", "revenue"): "13000000000000.0"}


def test_empty_rows_still_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """No rows: the summary table renders empty and the exit code is unchanged."""

    result, _calls = _invoke(monkeypatch, _payload())

    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert _summary_cells(result.output) == []
    assert "Metric" not in result.output.split("┏")[-1]


def test_request_payload_and_symbol_normalisation_are_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The POST body is byte-for-byte the pre-change contract."""

    result, calls = _invoke(
        monkeypatch,
        _payload(_row("7203.T", {"revenue": 1.0})),
        args=["7203.t", "6758.t", "--filter", "operating_margin:gt:0.1"],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        {
            "path": "/v1/screen",
            "json": {
                "symbols": ["7203.T", "6758.T"],
                "filters": [{"metric": "operating_margin", "operator": "gt", "value": 0.1}],
            },
        }
    ]


def test_invalid_filter_is_rejected_before_any_http_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, calls = _invoke(
        monkeypatch,
        _payload(),
        args=["7203.T", "--filter", "operating_margin"],
    )

    assert result.exit_code != 0
    assert "Invalid --filter" in result.output
    assert calls == []


def test_http_failure_reports_nothing_fabricated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing API surfaces as an error, never as an empty/zero-filled table."""

    result, calls = _invoke(
        monkeypatch,
        {"detail": "provider unavailable"},
        status_code=503,
    )

    assert result.exit_code != 0
    assert isinstance(result.exception, httpx.HTTPStatusError)
    assert "unavailable" not in result.output
    assert len(calls) == 1
