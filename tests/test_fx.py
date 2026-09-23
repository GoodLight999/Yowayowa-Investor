"""FX Phase1 tests: symbol normalization, Yahoo conversions, API surface, propose-only contract."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from starlette.testclient import TestClient

from yowayowa.config import get_settings
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.fx_models import (
    DEFAULT_FX_RISK_NOTE,
    FxDirection,
    FxHistory,
    FxHistoryPoint,
    FxProposalSpec,
    FxRateSnapshot,
    build_fx_proposal,
    fx_pair_to_yahoo,
    normalize_fx_pair,
    separate_rationale_tokens,
    yahoo_to_fx_pair,
)
from yowayowa.symbols import InputValidationError

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


def _provenance() -> Provenance:
    return Provenance(
        provider="fixture",
        source="Fixture",
        source_url=None,
        license_class=LicenseClass.PERSONAL_ONLY,
        retrieved_at=NOW,
        as_of=NOW,
    )


def _snapshot(pair: str = "USDJPY", rate: float = 150.0) -> FxRateSnapshot:
    return FxRateSnapshot(
        pair=pair,
        rate=rate,
        previous_close=149.0,
        as_of=NOW,
        provenance=_provenance(),
    )


# ---------------------------------------------------------------- normalization


def test_normalize_fx_pair_accepts_supported_pairs() -> None:
    assert normalize_fx_pair("USDJPY") == "USDJPY"
    assert normalize_fx_pair("EURGBP") == "EURGBP"


def test_normalize_fx_pair_rejects_three_letter_codes() -> None:
    with pytest.raises(InputValidationError):
        normalize_fx_pair("USDJ")
    with pytest.raises(InputValidationError):
        normalize_fx_pair("USJPYX")


def test_normalize_fx_pair_rejects_digits_and_symbols() -> None:
    with pytest.raises(InputValidationError):
        normalize_fx_pair("USDJP1")
    with pytest.raises(InputValidationError):
        normalize_fx_pair("USD/JPY")


def test_normalize_fx_pair_rejects_lowercase_without_repair() -> None:
    with pytest.raises(InputValidationError):
        normalize_fx_pair("usdjpy")


def test_normalize_fx_pair_rejects_unsupported_currencies() -> None:
    with pytest.raises(InputValidationError):
        normalize_fx_pair("XYZJPY")
    with pytest.raises(InputValidationError):
        normalize_fx_pair("RUBUSD")


def test_normalize_fx_pair_rejects_identical_base_and_quote() -> None:
    with pytest.raises(InputValidationError):
        normalize_fx_pair("USDUSD")


# ------------------------------------------------------------- yahoo conversion


def test_fx_pair_to_yahoo_usd_base_short_form() -> None:
    assert fx_pair_to_yahoo("USDJPY") == "JPY=X"
    assert fx_pair_to_yahoo("USDCHF") == "CHF=X"


def test_fx_pair_to_yahoo_crypto_uses_dash_form() -> None:
    assert fx_pair_to_yahoo("BTCUSD") == "BTC-USD"
    assert fx_pair_to_yahoo("ETHUSD") == "ETH-USD"


def test_fx_pair_to_yahoo_generic_cross_keeps_full_form() -> None:
    assert fx_pair_to_yahoo("EURJPY") == "EURJPY=X"


def test_yahoo_to_fx_pair_round_trips() -> None:
    assert yahoo_to_fx_pair("JPY=X") == "USDJPY"
    assert yahoo_to_fx_pair("BTC-USD") == "BTCUSD"
    assert yahoo_to_fx_pair("EURJPY=X") == "EURJPY"


def test_yahoo_to_fx_pair_rejects_invalid_symbols() -> None:
    with pytest.raises(InputValidationError):
        yahoo_to_fx_pair("USD=X")
    with pytest.raises(InputValidationError):
        yahoo_to_fx_pair("AAPL")


# ------------------------------------------------------------------ api surface


def _install_fx_app(monkeypatch: pytest.MonkeyPatch, tmp_path, provider: object) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{tmp_path / 'fx.db'}")
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    get_settings.cache_clear()
    from yowayowa.api import fx_routes

    monkeypatch.setattr(fx_routes, "yahoo_market_provider", lambda: provider)


def test_fx_rate_endpoint_returns_snapshot(tmp_path, monkeypatch) -> None:
    requested: list[str] = []

    class FakeProvider:
        def fx_quote(self, pair: str) -> FxRateSnapshot:
            requested.append(pair)
            if pair != "USDJPY":
                raise LookupError(f"No FX rate returned for {pair}")
            return _snapshot(pair)

    _install_fx_app(monkeypatch, tmp_path, FakeProvider())
    from yowayowa.api.app import app

    try:
        with TestClient(app) as client:
            response = client.get("/v1/fx/rate", params={"pair": "USDJPY"})
            assert response.status_code == 200
            payload = response.json()
            assert payload["pair"] == "USDJPY"
            assert payload["rate"] == 150.0
            assert payload["previous_close"] == 149.0
            assert payload["provenance"]["provider"] == "fixture"
            assert requested == ["USDJPY"]

            missing = client.get("/v1/fx/rate", params={"pair": "EURUSD"})
            assert missing.status_code == 404

            invalid = client.get("/v1/fx/rate", params={"pair": "usdjpy"})
            assert invalid.status_code == 422
    finally:
        get_settings.cache_clear()


def test_fx_rate_endpoint_rejects_unsupported_currency(tmp_path, monkeypatch) -> None:
    _install_fx_app(monkeypatch, tmp_path, object())

    from yowayowa.api.app import app

    try:
        with TestClient(app) as client:
            response = client.get("/v1/fx/rate", params={"pair": "XYZJPY"})
            assert response.status_code == 422
            assert "Unsupported FX currency code" in response.json()["detail"]
    finally:
        get_settings.cache_clear()


def test_fx_history_endpoint_returns_points_with_none_preserved(tmp_path, monkeypatch) -> None:
    class FakeProvider:
        def fx_history(
            self,
            pair: str,
            *,
            interval: str = "1d",
            period: str = "1mo",
        ) -> FxHistory:
            assert (interval, period) == ("1h", "1mo")
            return FxHistory(
                pair=pair,
                interval=interval,
                points=[
                    FxHistoryPoint(
                        timestamp=NOW,
                        open=149.5,
                        high=150.5,
                        low=149.2,
                        close=150.0,
                        volume=None,
                    ),
                    FxHistoryPoint(timestamp=NOW, open=None, high=None, low=None, close=None),
                ],
                provenance=_provenance(),
            )

    _install_fx_app(monkeypatch, tmp_path, FakeProvider())
    from yowayowa.api.app import app

    try:
        with TestClient(app) as client:
            response = client.get(
                "/v1/fx/history",
                params={"pair": "USDJPY", "interval": "1h", "period": "1mo"},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["pair"] == "USDJPY"
            assert payload["interval"] == "1h"
            assert payload["points"][0]["volume"] is None
            assert payload["points"][1]["close"] is None

            unsupported = client.get(
                "/v1/fx/history",
                params={"pair": "USDJPY", "interval": "2h"},
            )
            assert unsupported.status_code == 422
    finally:
        get_settings.cache_clear()


def test_fx_provider_fx_history_preserves_partial_rows(tmp_path, monkeypatch) -> None:
    """The real provider path keeps partially-filled bars instead of zero-filling."""

    import pandas as pd

    from yowayowa.providers.yahoo import YahooMarketProvider

    frame = pd.DataFrame(
        {
            "Open": [149.5, float("nan")],
            "High": [150.5, float("nan")],
            "Low": [149.2, float("nan")],
            "Close": [150.0, 150.4],
            "Volume": [float("nan"), float("nan")],
        },
        index=pd.DatetimeIndex(
            [datetime(2026, 9, 22, tzinfo=UTC), datetime(2026, 9, 23, tzinfo=UTC)],
            name="Date",
        ),
    )

    class StubTicker:
        def __init__(self, symbol: str) -> None:
            assert symbol == "JPY=X"

        def history(self, **_: object) -> pd.DataFrame:
            return frame

    monkeypatch.setattr("yowayowa.providers.yahoo.yf.Ticker", StubTicker)
    provider = YahooMarketProvider.__new__(YahooMarketProvider)
    provider.settings = get_settings()
    provider._fx_history_cache = {}

    result = provider.fx_history("USDJPY", interval="1d", period="5d")
    assert [point.close for point in result.points] == [150.0, 150.4]
    assert result.points[0].volume is None
    assert result.points[1].open is None
    assert result.points[1].high is None
    assert result.provenance.provider == "yahoo/yfinance"


# ------------------------------------------------------- proposal schema contract


def test_fx_proposal_action_is_fixed_propose_only() -> None:
    spec = build_fx_proposal("USDJPY", FxDirection.FLAT, None, _snapshot())
    assert spec.action == "propose_only"
    assert FxProposalSpec.model_fields["action"].default == "propose_only"
    with pytest.raises(ValueError):
        FxProposalSpec.model_validate(
            {
                "action": "submit_order",
                "pair": "USDJPY",
                "direction": "long",
                "generated_at": NOW,
            }
        )


def test_fx_proposal_separates_sourced_facts_from_inference() -> None:
    spec = build_fx_proposal("USDJPY", FxDirection.LONG, 0.4, _snapshot())
    sourced, inference = separate_rationale_tokens(spec.rationale_tokens)
    assert sourced == [
        "sourced:rate=150.0",
        f"sourced:as_of={NOW.isoformat()}",
        "sourced:change_1d=+0.006711",
    ]
    assert inference == ["inference:momentum=up"]
    assert spec.direction == "long"
    assert spec.strength == 0.4
    assert spec.risk_note == DEFAULT_FX_RISK_NOTE
    with pytest.raises(InputValidationError):
        separate_rationale_tokens(["unclassified claim"])


def test_fx_proposal_fail_closed_on_missing_previous_close() -> None:
    quote = _snapshot().model_copy(update={"previous_close": None})
    spec = build_fx_proposal("USDJPY", FxDirection.FLAT, None, quote)
    sourced, inference = separate_rationale_tokens(spec.rationale_tokens)
    assert not any(token.startswith("sourced:change_1d=") for token in sourced)
    assert inference == []
    assert spec.strength is None

    zero = _snapshot().model_copy(update={"previous_close": 0.0})
    zero_spec = build_fx_proposal("USDJPY", FxDirection.FLAT, None, zero)
    assert not any(token.startswith("sourced:change_1d=") for token in zero_spec.rationale_tokens)


def test_fx_proposal_endpoint_returns_spec(tmp_path, monkeypatch) -> None:
    _install_fx_app(monkeypatch, tmp_path, _SnapshotProvider())
    from yowayowa.api.app import app

    try:
        with TestClient(app) as client:
            response = client.get(
                "/v1/fx/proposal",
                params={"pair": "USDJPY", "direction": "long", "strength": 0.5},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["action"] == "propose_only"
            assert payload["pair"] == "USDJPY"
            assert payload["direction"] == "long"
            assert payload["strength"] == 0.5
            assert payload["rationale_tokens"] == [
                "sourced:rate=150.0",
                f"sourced:as_of={NOW.isoformat()}",
                "sourced:change_1d=+0.006711",
                "inference:momentum=up",
            ]
            assert payload["provenance"]["provider"] == "fixture"
            assert "risk_note" in payload

            invalid_direction = client.get(
                "/v1/fx/proposal",
                params={"pair": "USDJPY", "direction": "aggressive"},
            )
            assert invalid_direction.status_code == 422
    finally:
        get_settings.cache_clear()


class _SnapshotProvider:
    def fx_quote(self, pair: str) -> FxRateSnapshot:
        return _snapshot(pair)
