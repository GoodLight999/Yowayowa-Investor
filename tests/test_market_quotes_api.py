from datetime import UTC, datetime

from starlette.testclient import TestClient

from yowayowa.config import get_settings
from yowayowa.domain import LicenseClass, MarketQuote, MarketQuoteBatch, Provenance


def test_market_quotes_api_batches_normalized_symbols(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{tmp_path / 'quotes.db'}")
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    get_settings.cache_clear()

    from yowayowa.api import routes
    from yowayowa.api.app import app

    requested: list[list[str]] = []
    now = datetime(2026, 8, 12, tzinfo=UTC)

    class FakeProvider:
        def quotes(self, symbols: list[str]) -> MarketQuoteBatch:
            requested.append(symbols)
            return MarketQuoteBatch(
                quotes={
                    symbol: MarketQuote(
                        symbol=symbol,
                        price=20.0 if symbol == "RKLB" else 45.0,
                        previous_close=19.0 if symbol == "RKLB" else 46.0,
                        as_of=now,
                    )
                    for symbol in symbols
                },
                provenance=Provenance(
                    provider="fixture",
                    source="Fixture",
                    source_url=None,
                    license_class=LicenseClass.PERSONAL_ONLY,
                    retrieved_at=now,
                    as_of=now,
                ),
            )

    monkeypatch.setattr(routes, "yahoo_market_provider", lambda: FakeProvider())

    try:
        with TestClient(app) as client:
            response = client.get("/v1/markets/quotes", params={"symbols": "rklb, ASTS,rklb"})
            assert response.status_code == 200
            payload = response.json()
            assert requested == [["RKLB", "ASTS"]]
            assert list(payload["quotes"]) == ["RKLB", "ASTS"]
            assert payload["quotes"]["RKLB"]["price"] == 20.0

            invalid = client.get("/v1/markets/quotes", params={"symbols": "<script>"})
            assert invalid.status_code == 422
    finally:
        get_settings.cache_clear()


def test_market_history_invalid_indicator_returns_422(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    get_settings.cache_clear()

    from yowayowa.api import routes
    from yowayowa.api.app import app

    class InvalidIndicatorProvider:
        def history(
            self,
            symbol: str,
            period: str,
            interval: str,
            indicators: list[str],
        ) -> None:
            raise ValueError(
                "Unknown indicator 'magic14'. Supported forms: sma20, ema20, rsi14, "
                "bb20, atr14, macd, macd12-26-9"
            )

    monkeypatch.setattr(routes, "yahoo_market_provider", lambda: InvalidIndicatorProvider())

    try:
        with TestClient(app) as client:
            response = client.get(
                "/v1/markets/RKLB/history",
                params={"indicators": "magic14"},
            )
            assert response.status_code == 422
            assert "Unknown indicator 'magic14'" in response.json()["detail"]
    finally:
        get_settings.cache_clear()
