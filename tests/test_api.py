from starlette.testclient import TestClient

from yowayowa.config import get_settings


def _reset_settings() -> None:
    get_settings.cache_clear()


def test_api_startup_health_watchlists_compare_and_portfolio_surfaces(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{tmp_path / 'personal.db'}")
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    _reset_settings()

    from yowayowa.api.app import app

    try:
        with TestClient(app) as client:
            health = client.get("/v1/health")
            assert health.status_code == 200
            assert health.json()["status"] == "ok"
            assert health.json()["mode"] == "personal"

            japanese = client.get("/?lang=ja")
            assert japanese.status_code == 200
            assert '<html lang="ja">' in japanese.text
            assert ">ホーム</h1>" in japanese.text
            assert "yowayowa_lang=ja" in japanese.headers["set-cookie"]

            english = client.get("/?lang=en")
            assert english.status_code == 200
            assert '<html lang="en">' in english.text
            assert ">Home</h1>" in english.text
            assert "yowayowa_lang=en" in english.headers["set-cookie"]

            persisted_english = client.get("/markets")
            assert persisted_english.status_code == 200
            assert '<html lang="en">' in persisted_english.text
            assert ">Markets</h1>" in persisted_english.text

            watchlists = client.get("/v1/watchlists")
            assert watchlists.status_code == 200
            assert [row["name"] for row in watchlists.json()] == ["Main"]
            main_watchlist_id = watchlists.json()[0]["id"]

            created_watchlist = client.post("/v1/watchlists", json={"name": "High Conviction"})
            assert created_watchlist.status_code == 201
            assert created_watchlist.json()["name"] == "High Conviction"

            invalid_watchlist_symbol = client.post(
                f"/v1/watchlists/{main_watchlist_id}/symbols",
                json=["<script>"],
            )
            assert invalid_watchlist_symbol.status_code == 422

            compare_page = client.get("/compare")
            assert compare_page.status_code == 200
            assert ">Compare</h1>" in compare_page.text

            metric_catalog = client.get("/v1/compare/metrics")
            assert metric_catalog.status_code == 200
            assert any(row["key"] == "revenue_growth_yoy" for row in metric_catalog.json())

            portfolio_page = client.get("/portfolio")
            assert portfolio_page.status_code == 200
            assert ">Portfolio</h1>" in portfolio_page.text

            invalid_portfolio = client.post(
                "/v1/portfolios",
                json={"name": "Invalid", "base_currency": "US"},
            )
            assert invalid_portfolio.status_code == 422

            created_portfolio = client.post(
                "/v1/portfolios",
                json={"name": "Core", "base_currency": "USD"},
            )
            assert created_portfolio.status_code == 201
            portfolio_id = created_portfolio.json()["id"]

            fetched = client.get(f"/v1/portfolios/{portfolio_id}")
            assert fetched.status_code == 200
            assert fetched.json()["name"] == "Core"
            assert fetched.json()["positions"] == []

            invalid_symbol = client.put(
                f"/v1/portfolios/{portfolio_id}/positions",
                json={
                    "symbol": "<script>",
                    "quantity": "1",
                    "average_cost": "8.25",
                    "currency": "USD",
                },
            )
            assert invalid_symbol.status_code == 422

            invalid_currency = client.put(
                f"/v1/portfolios/{portfolio_id}/positions",
                json={
                    "symbol": "RKLB",
                    "quantity": "1",
                    "average_cost": "8.25",
                    "currency": "USDX",
                },
            )
            assert invalid_currency.status_code == 422

            position = client.put(
                f"/v1/portfolios/{portfolio_id}/positions",
                json={
                    "symbol": "rklb",
                    "quantity": "12.5",
                    "average_cost": "8.25",
                    "currency": "usd",
                },
            )
            assert position.status_code == 200
            assert position.json()["positions"][0]["symbol"] == "RKLB"
            assert position.json()["positions"][0]["currency"] == "USD"

            removed = client.delete(f"/v1/portfolios/{portfolio_id}/positions/RKLB")
            assert removed.status_code == 200
            assert removed.json()["positions"] == []
    finally:
        _reset_settings()


def test_public_mode_requires_bearer_token(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "public")
    monkeypatch.setenv("YOWAYOWA_API_TOKEN", "test-secret")
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{tmp_path / 'public.db'}")
    _reset_settings()

    from yowayowa.api.app import app

    try:
        with TestClient(app) as client:
            unauthorized = client.get("/v1/health")
            assert unauthorized.status_code == 401

            authorized = client.get("/v1/health", headers={"Authorization": "Bearer test-secret"})
            assert authorized.status_code == 200
            assert authorized.json()["mode"] == "public"
    finally:
        _reset_settings()
