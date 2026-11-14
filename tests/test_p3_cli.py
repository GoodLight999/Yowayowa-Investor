from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from yowayowa.cli_entry import app

runner = CliRunner()


def _bucket(**overrides: Any) -> dict[str, Any]:
    """Build a dict matching StrategyCalibrationBucket's real fields."""
    bucket: dict[str, Any] = {
        "strategy_id": "momentum-12-1",
        "scoring_version": "v1",
        "horizon_trading_days": 20,
        "sample_total": 120,
        "sample_available": 100,
        "sample_pending": 15,
        "sample_unavailable": 5,
        "minimum_sample_warning": False,
        "score_deciles": [
            {
                "decile": 1,
                "score_min": 0.1,
                "score_max": 0.4,
                "sample_count": 10,
                "median_total_return": 0.083,
                "median_excess_return": 0.01,
            },
            {
                "decile": 10,
                "score_min": 2.5,
                "score_max": 4.0,
                "sample_count": 10,
                "median_total_return": -0.02,
                "median_excess_return": None,
            },
        ],
        "factor_deciles": [
            {
                "decile": 1,
                "factor_key": "momentum",
                "factor_score_fraction_min": 0.0,
                "factor_score_fraction_max": 0.1,
                "sample_count": 10,
                "median_total_return": 0.05,
                "median_excess_return": 0.005,
            }
        ],
        "median_total_return": 0.083,
        "mean_total_return": 0.09,
        "median_excess_return": 0.02,
        "mean_excess_return": 0.025,
        "positive_excess_hit_rate": 0.55,
        "rank_ic": 0.25,
        "ic_sample_count": 60,
        "ic_insufficient": False,
        "notes": [],
        "oos_rank_ic": 0.31,
        "oos_ic_sample_count": 20,
        "oos_ic_insufficient": True,
        "oos_median_total_return": 0.04,
        "oos_mean_total_return": 0.05,
        "oos_median_excess_return": 0.01,
        "oos_mean_excess_return": 0.015,
        "oos_positive_excess_hit_rate": 0.52,
        "is_rank_ic": 0.28,
        "is_ic_sample_count": 40,
        "is_ic_insufficient": False,
        "is_median_total_return": 0.06,
        "is_mean_total_return": 0.07,
        "is_median_excess_return": 0.015,
        "is_mean_excess_return": 0.02,
        "is_positive_excess_hit_rate": 0.54,
        "oos_sample_count": 25,
        "is_sample_count": 95,
        "purged_count": 3,
        "exit_at_unknown_count": 2,
        "oos_split_at": "2026-01-01T00:00:00+00:00",
        "oos_median_total_return_ci_low": -0.01,
        "oos_median_total_return_ci_high": 0.09,
        "oos_median_excess_return_ci_low": -0.02,
        "oos_median_excess_return_ci_high": 0.03,
        "oos_notes": ["oos-window-rolled"],
    }
    bucket.update(overrides)
    return bucket


def _report(buckets: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "buckets": buckets,
        "provenance": ["strategy-forward-outcomes"],
        "notes": ["evaluated"],
        "evaluated_at": "2026-09-29T00:00:00+00:00",
    }


class _Response:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def raise_for_status(self) -> _Response:
        return self

    def json(self) -> Any:
        return self._payload


class _StubClient:
    """Stub of yowayowa.cli._client that records calls and returns canned payloads."""

    def __init__(self, calls: list[dict[str, Any]], routes: dict[str, Any]) -> None:
        self._calls = calls
        self._routes = routes

    def __enter__(self) -> _StubClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def get(self, path: str, params: dict[str, Any] | None = None) -> _Response:
        self._calls.append({"method": "GET", "path": path, "params": params})
        for suffix, payload in self._routes.items():
            if path.endswith(suffix):
                status = 422 if isinstance(payload, dict) and payload.get("detail") else 200
                return _Response(payload, status_code=status)
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path: str, json: Any = None) -> _Response:
        self._calls.append({"method": "POST", "path": path, "json": json})
        for suffix, payload in self._routes.items():
            if path.endswith(suffix):
                status = 422 if isinstance(payload, dict) and payload.get("detail") else 200
                return _Response(payload, status_code=status)
        raise AssertionError(f"unexpected POST {path}")


def _route_key(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1]


def _stub(
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    status_by_suffix: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Patch yowayowa.cli._client (imported into each CLI module) and return calls."""
    calls: list[dict[str, Any]] = []
    statuses = status_by_suffix or {}

    class _RoutedClient(_StubClient):
        def _status(self, path: str) -> int:
            return statuses.get(_route_key(path), 200)

        def get(self, path: str, params: dict[str, Any] | None = None) -> _Response:
            self._calls.append({"method": "GET", "path": path, "params": params})
            for suffix, payload in self._routes.items():
                if path.endswith(suffix):
                    return _Response(payload, status_code=self._status(path))
            raise AssertionError(f"unexpected GET {path}")

        def post(self, path: str, json: Any = None) -> _Response:
            self._calls.append({"method": "POST", "path": path, "json": json})
            for suffix, payload in self._routes.items():
                if path.endswith(suffix):
                    return _Response(payload, status_code=self._status(path))
            raise AssertionError(f"unexpected POST {path}")

    import yowayowa.calibration_cli as calibration_cli
    import yowayowa.cli as cli_module
    import yowayowa.cli_entry as cli_entry
    import yowayowa.hypothesis_cli as hypothesis_cli

    client_factory = lambda base_url, token: _RoutedClient(calls, routes)  # noqa: E731
    for module in (cli_module, cli_entry, calibration_cli, hypothesis_cli):
        monkeypatch.setattr(module, "_client", client_factory)
    return calls


# ---------------------------------------------------------------------------
# 1. --help exits 0 for the six P3 surfaces
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["strategy-calibration", "--help"],
        ["hypothesis", "--help"],
        ["hypothesis", "add", "--help"],
        ["hypothesis", "list", "--help"],
        ["hypothesis", "show", "--help"],
        ["portfolio", "sizing-proposals", "--help"],
    ],
)
def test_p3_help_exits_zero(args: list[str]) -> None:
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert result.exception is None


# ---------------------------------------------------------------------------
# 2. calibration happy path
# ---------------------------------------------------------------------------


def test_calibration_renders_bucket_statistics(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(
        monkeypatch,
        {"calibration": _report([_bucket()])},
    )
    result = runner.invoke(app, ["strategy-calibration"])

    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert calls[0]["method"] == "GET"
    assert calls[0]["path"] == "/v1/strategy-research/calibration"
    # Rates are rendered as percentages...
    assert "+8.30%" in result.output
    # ...but the rank IC coefficient must never be percentified (regression).
    assert "0.25" in result.output
    assert "rank_ic: 0.25 (n=60)" in result.output
    assert "+300.00%" not in result.output
    assert "purged_count: 3" in result.output
    assert "None" not in result.output
    assert "—" in result.output


def test_calibration_insufficient_ic_hides_coefficients(monkeypatch: pytest.MonkeyPatch) -> None:
    bucket = _bucket(ic_insufficient=True, oos_ic_insufficient=True)
    bucket["rank_ic"] = 0.25
    bucket["oos_rank_ic"] = 0.31
    _stub(monkeypatch, {"calibration": _report([bucket])})
    result = runner.invoke(app, ["strategy-calibration"])

    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert "insufficient (n=60)" in result.output
    assert "Out-of-sample IC: insufficient (n=20)" in result.output
    assert "0.25" not in result.output
    assert "0.31" not in result.output
    assert "+31.00%" not in result.output


def test_calibration_ic_available_prints_coefficients(monkeypatch: pytest.MonkeyPatch) -> None:
    bucket = _bucket(
        ic_insufficient=False,
        oos_ic_insufficient=False,
        oos_ic_sample_count=20,
        is_ic_insufficient=False,
    )
    _stub(monkeypatch, {"calibration": _report([bucket])})
    result = runner.invoke(app, ["strategy-calibration"])

    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert "Out-of-sample IC: 0.31 (n=20)" in result.output
    assert "In-sample IC: 0.28 (n=40)" in result.output


def test_calibration_segments_are_labelled(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, {"calibration": _report([_bucket()])})
    result = runner.invoke(app, ["strategy-calibration"])

    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert "Out-of-sample:" in result.output
    assert "In-sample:" in result.output
    assert "Split:" in result.output
    assert "oos_median_total_return: +4.00%" in result.output
    assert "is_median_total_return: +6.00%" in result.output
    assert "oos_split_at: 2026-01-01T00:00:00+00:00" in result.output
    assert "time-series front-half" in result.output
    assert "not a fitted-model in-sample fit" in result.output
    assert "OOS notes: oos-window-rolled" in result.output
    assert "Note: evaluated" in result.output
    assert "Provenance: strategy-forward-outcomes" in result.output


# ---------------------------------------------------------------------------
# 3. calibration edge / failure paths
# ---------------------------------------------------------------------------


def test_calibration_empty_buckets(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, {"calibration": _report([])})
    result = runner.invoke(app, ["strategy-calibration"])

    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert "No calibration buckets" in result.output


def test_calibration_http_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(
        monkeypatch,
        {"calibration": {"detail": "horizons must be positive"}},
        status_by_suffix={"calibration": 422},
    )
    result = runner.invoke(app, ["strategy-calibration"])

    assert result.exit_code == 1
    assert "HTTP 422" in result.output
    assert "horizons must be positive" in result.output


def test_calibration_params_defaults_and_optionals(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, {"calibration": _report([])})

    result = runner.invoke(app, ["strategy-calibration"])
    assert result.exit_code == 0, result.output
    assert calls[0]["params"] == {"horizons": "20,60,120", "limit": 50}
    assert "oos_min_sample" not in calls[0]["params"]

    result = runner.invoke(
        app,
        [
            "strategy-calibration",
            "--oos-min-sample",
            "15",
            "--strategy-id",
            "mom",
            "--region",
            "jp",
            "--symbol",
            "7203",
            "--benchmark",
            "TOPIX",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls[1]["params"]["oos_min_sample"] == 15
    assert calls[1]["params"]["strategy_id"] == "mom"
    assert calls[1]["params"]["region"] == "jp"
    assert calls[1]["params"]["symbol"] == "7203"
    assert calls[1]["params"]["benchmark"] == "TOPIX"


# ---------------------------------------------------------------------------
# 5. hypothesis add
# ---------------------------------------------------------------------------


def _add_args() -> list[str]:
    return [
        "hypothesis",
        "add",
        "--hypothesis",
        "BOJ taper lifts JPY",
        "--criteria",
        "USDJPY < 140",
        "--criteria",
        "carry spread inverts",
        "--evidence-url",
        "https://example.com/boj",
        "--symbol",
        "USDJPY",
    ]


def test_hypothesis_add_posts_body(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, {"hypotheses": {"id": 7}})
    result = runner.invoke(app, _add_args())

    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/v1/hypotheses"
    body = call["json"]
    assert body["falsification_criteria"] == ["USDJPY < 140", "carry spread inverts"]
    assert body["evidence_links"][0]["source_url"] == "https://example.com/boj"
    assert body["symbol"] == "USDJPY"
    assert "Saved hypothesis 7" in result.output


def test_hypothesis_add_requires_criteria(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, {"hypotheses": {"id": 7}})
    result = runner.invoke(
        app,
        [
            "hypothesis",
            "add",
            "--hypothesis",
            "h",
            "--evidence-url",
            "https://example.com",
        ],
    )
    assert result.exit_code != 0
    assert calls == []


def test_hypothesis_add_requires_evidence_url(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, {"hypotheses": {"id": 7}})
    result = runner.invoke(
        app,
        ["hypothesis", "add", "--hypothesis", "h", "--criteria", "c"],
    )
    assert result.exit_code != 0
    assert calls == []


def test_hypothesis_add_rejects_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, {"hypotheses": {"id": 7}})
    result = runner.invoke(
        app,
        ["hypothesis", "add", "--hypothesis", "   ", "--criteria", "c", "--evidence-url", "u"],
    )
    assert result.exit_code != 0
    assert calls == []


def test_hypothesis_add_http_403(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(
        monkeypatch, {"hypotheses": {"detail": "forbidden"}}, status_by_suffix={"hypotheses": 403}
    )
    result = runner.invoke(app, _add_args())

    assert result.exit_code == 1
    assert "HTTP 403" in result.output


# ---------------------------------------------------------------------------
# 6. hypothesis list / show
# ---------------------------------------------------------------------------


def test_hypothesis_list_with_symbol_param(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, {"hypotheses": []})
    result = runner.invoke(app, ["hypothesis", "list", "--symbol", "7203"])

    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert calls[0]["params"]["symbol"] == "7203"


def test_hypothesis_list_without_symbol_param(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, {"hypotheses": []})
    result = runner.invoke(app, ["hypothesis", "list"])

    assert result.exit_code == 0, result.output
    assert "symbol" not in calls[0]["params"]


def test_hypothesis_list_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, {"hypotheses": []})
    result = runner.invoke(app, ["hypothesis", "list"])

    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert "No hypotheses" in result.output


def test_hypothesis_show_renders_details(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(
        monkeypatch,
        {
            "hypotheses": {},
            "7": {
                "id": 7,
                "hypothesis": "BOJ taper lifts JPY",
                "symbol": "USDJPY",
                "falsification_criteria": ["USDJPY < 140", "carry spread inverts"],
                "provenance": {
                    "created_at": "2026-09-29T00:00:00+00:00",
                    "evidence_links": [
                        {
                            "source_url": "https://example.com/boj",
                            "provider": "boj",
                            "source": "minutes",
                            "retrieved_at": "2026-09-28T00:00:00+00:00",
                            "as_of": "2026-09-27T00:00:00+00:00",
                        }
                    ],
                },
            },
        },
    )
    result = runner.invoke(app, ["hypothesis", "show", "7"])

    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert "BOJ taper lifts JPY" in result.output
    assert "USDJPY < 140" in result.output
    assert "carry spread inverts" in result.output
    assert "2026-09-29T00:00:00+00:00" in result.output


def test_hypothesis_show_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(
        monkeypatch,
        {"999": {"detail": "hypothesis not found"}},
        status_by_suffix={"999": 404},
    )
    result = runner.invoke(app, ["hypothesis", "show", "999"])

    assert result.exit_code == 1
    assert "HTTP 404" in result.output


# ---------------------------------------------------------------------------
# 7. sizing-proposals
# ---------------------------------------------------------------------------


def _idea_json() -> str:
    return json.dumps(
        {
            "symbol": "7203",
            "direction": "buy",
            "entry_price": 2500.0,
            "stop_loss_price": 2400.0,
        }
    )


def _proposal_payload() -> dict[str, Any]:
    return {
        "portfolio_id": 5,
        "base_currency": "JPY",
        "gross_market_value_base": 1_000_000.0,
        "risk_budget_pct": 0.02,
        "total_risk_budget_base": 20000.0,
        "per_idea_risk_budget_base": 20000.0,
        "max_position_pct": 0.1,
        "max_position_value_base": 100000.0,
        "executable": False,
        "notes": ["proposal only"],
        "provenance": ["sizing-engine"],
        "ideas": [
            {
                "symbol": "7203",
                "quantity": 100,
                "proposed_notional_base": 250000.0,
                "estimated_stop_loss_base": 10000.0,
                "projected_position_value_base": 260000.0,
                "limiting_constraints": ["risk_budget"],
                "risk_per_share_base": 100.0,
                "risk_limited_quantity": 200,
                "position_limited_quantity": 40,
            }
        ],
    }


def test_sizing_proposals_posts_body(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, {"sizing-proposals": _proposal_payload()})
    result = runner.invoke(
        app,
        [
            "portfolio",
            "sizing-proposals",
            "5",
            "--idea",
            _idea_json(),
            "--risk-budget-pct",
            "0.02",
            "--max-position-pct",
            "0.1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "/v1/portfolios/5/sizing-proposals"
    body = calls[0]["json"]
    assert body["ideas"][0] == json.loads(_idea_json())
    assert body["risk_budget_pct"] == 0.02
    assert body["max_position_pct"] == 0.1
    assert "100" in result.output  # quantity cell of the proposal table
    assert "not executable" in result.output


def test_sizing_proposals_requires_idea(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, {"sizing-proposals": _proposal_payload()})
    result = runner.invoke(
        app,
        [
            "portfolio",
            "sizing-proposals",
            "5",
            "--risk-budget-pct",
            "0.02",
            "--max-position-pct",
            "0.1",
        ],
    )
    assert result.exit_code != 0
    assert calls == []


def test_sizing_proposals_rejects_bad_json(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, {"sizing-proposals": _proposal_payload()})
    result = runner.invoke(
        app,
        [
            "portfolio",
            "sizing-proposals",
            "5",
            "--idea",
            "{bad json",
            "--risk-budget-pct",
            "0.02",
            "--max-position-pct",
            "0.1",
        ],
    )
    assert result.exit_code != 0
    assert calls == []


def test_sizing_proposals_http_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(
        monkeypatch,
        {"sizing-proposals": {"detail": "invalid idea"}},
        status_by_suffix={"sizing-proposals": 422},
    )
    result = runner.invoke(
        app,
        [
            "portfolio",
            "sizing-proposals",
            "5",
            "--idea",
            _idea_json(),
            "--risk-budget-pct",
            "0.02",
            "--max-position-pct",
            "0.1",
        ],
    )
    assert result.exit_code == 1
    assert "HTTP 422" in result.output
