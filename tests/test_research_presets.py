from starlette.testclient import TestClient

from yowayowa.config import get_settings


def test_research_preset_api_validates_persists_updates_and_deletes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{tmp_path / 'presets.db'}")
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    get_settings.cache_clear()

    from yowayowa.api.app import app

    chart_payload = {
        "sources": [
            {"id": "px", "source": "price", "symbol": "RKLB", "period": "2y"},
            {"id": "bench", "source": "price", "symbol": "^GSPC", "period": "2y"},
        ],
        "transforms": [
            {
                "id": "corr",
                "kind": "rolling_correlation",
                "left": "px",
                "right": "bench",
                "window": 60,
            }
        ],
    }
    screen_payload = {
        "symbols": ["RKLB", "ASTS"],
        "filters": [{"metric": "revenue_growth_yoy", "operator": "gt", "value": 0.2}],
    }
    compare_payload = {
        "symbols": ["RKLB", "ASTS", "HOOD"],
        "metrics": ["revenue_growth_yoy", "free_cash_flow_margin", "return_on_equity"],
    }

    with TestClient(app) as client:
        invalid_chart = client.post(
            "/v1/research-presets",
            json={
                "name": "Broken chart",
                "kind": "chart",
                "payload": {"sources": [], "transforms": []},
            },
        )
        assert invalid_chart.status_code == 422

        invalid_compare = client.post(
            "/v1/research-presets",
            json={
                "name": "Broken comparison",
                "kind": "compare",
                "payload": {"symbols": ["RKLB"], "metrics": ["return_on_equity"]},
            },
        )
        assert invalid_compare.status_code == 422

        unknown_compare_metric = client.post(
            "/v1/research-presets",
            json={
                "name": "Unknown metric",
                "kind": "compare",
                "payload": {"symbols": ["RKLB", "ASTS"], "metrics": ["magic_alpha"]},
            },
        )
        assert unknown_compare_metric.status_code == 422
        assert "Unknown comparison metrics" in unknown_compare_metric.json()["detail"]

        chart = client.post(
            "/v1/research-presets",
            json={"name": "Risk pair", "kind": "chart", "payload": chart_payload},
        )
        assert chart.status_code == 201
        chart_id = chart.json()["id"]
        assert chart.json()["payload"]["sources"][0]["period"] == "2y"

        screen = client.post(
            "/v1/research-presets",
            json={"name": "Growth", "kind": "screener", "payload": screen_payload},
        )
        assert screen.status_code == 201
        screen_id = screen.json()["id"]

        comparison = client.post(
            "/v1/research-presets",
            json={"name": "Space fintech", "kind": "compare", "payload": compare_payload},
        )
        assert comparison.status_code == 201
        comparison_id = comparison.json()["id"]
        assert comparison.json()["payload"] == compare_payload

        duplicate = client.post(
            "/v1/research-presets",
            json={"name": "Growth", "kind": "screener", "payload": screen_payload},
        )
        assert duplicate.status_code == 409

        all_presets = client.get("/v1/research-presets")
        assert all_presets.status_code == 200
        assert {(item["kind"], item["name"]) for item in all_presets.json()} == {
            ("chart", "Risk pair"),
            ("compare", "Space fintech"),
            ("screener", "Growth"),
        }

        screen_only = client.get("/v1/research-presets", params={"kind": "screener"})
        assert [item["id"] for item in screen_only.json()] == [screen_id]
        compare_only = client.get("/v1/research-presets", params={"kind": "compare"})
        assert [item["id"] for item in compare_only.json()] == [comparison_id]

        updated = client.put(
            f"/v1/research-presets/{screen_id}",
            json={
                "name": "Growth plus",
                "kind": "screener",
                "payload": {
                    "symbols": ["RKLB", "ASTS", "HOOD"],
                    "filters": screen_payload["filters"],
                },
            },
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Growth plus"
        assert updated.json()["payload"]["symbols"] == ["RKLB", "ASTS", "HOOD"]

        fetched = client.get(f"/v1/research-presets/{chart_id}")
        assert fetched.status_code == 200
        assert fetched.json()["name"] == "Risk pair"

        removed = client.delete(f"/v1/research-presets/{chart_id}")
        assert removed.status_code == 200
        assert removed.json()["name"] == "Risk pair"
        assert client.get(f"/v1/research-presets/{chart_id}").status_code == 404
