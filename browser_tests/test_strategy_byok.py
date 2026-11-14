import json

from playwright.sync_api import Page, Route

BASE_URL = "http://127.0.0.1:8000"
STRATEGY_ID = "kiyohara_global_value_growth"


def test_kiyohara_evaluator_receives_tab_scoped_edinet_key(page: Page) -> None:
    captured: dict[str, str] = {}

    def handler(route: Route) -> None:
        captured.update(route.request.headers)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "strategy_id": STRATEGY_ID,
                    "evaluations": [],
                    "errors": {},
                    "supplement_errors": {},
                    "evaluated_at": "2026-08-25T00:00:00Z",
                }
            ),
        )

    page.route(f"**/v1/strategy-presets/{STRATEGY_ID}/evaluate", handler)
    page.add_init_script(
        "sessionStorage.setItem('yowayowa.datasource.edinet.key.v1', 'browser-edinet-secret')"
    )
    page.goto(f"{BASE_URL}/screener?lang=en", wait_until="networkidle")
    page.evaluate(
        """async strategyId => {
          await fetch(`/v1/strategy-presets/${strategyId}/evaluate`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({candidates: [{symbol: '7203.T', market_cap: 100, pe_ratio: 10}]})
          }).then(response => response.json());
        }""",
        STRATEGY_ID,
    )

    assert captured.get("x-yowayowa-edinet-key") == "browser-edinet-secret"
