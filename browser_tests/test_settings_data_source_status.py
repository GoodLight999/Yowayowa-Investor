import json

from playwright.sync_api import Page, Route, expect

BASE_URL = "http://127.0.0.1:8000"


def _json(route: Route, payload: object) -> None:
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def _data_status() -> dict[str, bool]:
    return {
        "sec": True,
        "yahoo_personal": True,
        "edinet": False,
        "estat": False,
        "fred": False,
        "bea": False,
    }


def test_edinet_browser_key_updates_data_source_status(page: Page) -> None:
    page.route("**/v1/ai/providers", lambda route: _json(route, []))
    page.route("**/v1/settings/status", lambda route: _json(route, _data_status()))

    page.goto(f"{BASE_URL}/settings?lang=ja", wait_until="networkidle")

    edinet_status = page.locator('[data-source="edinet"] span')
    expect(edinet_status).to_have_text("未設定")

    page.locator("#edinet-browser-key").fill("browser-edinet-key")
    page.locator("#save-edinet-browser-key").click()
    expect(page.locator("#edinet-browser-key-status")).to_have_text("このタブで利用可能")
    expect(edinet_status).to_have_text("このタブで利用可能")

    page.reload(wait_until="networkidle")
    expect(page.locator('[data-source="edinet"] span')).to_have_text("このタブで利用可能")
    expect(page.locator("#edinet-browser-key")).to_have_value("browser-edinet-key")

    page.locator("#clear-edinet-browser-key").click()
    expect(page.locator("#edinet-browser-key-status")).to_have_text("未設定")
    expect(page.locator('[data-source="edinet"] span')).to_have_text("未設定")


def test_ai_api_key_can_be_removed_without_closing_the_tab(page: Page) -> None:
    providers = [
        {
            "id": "openrouter",
            "label": "OpenRouter",
            "adapter": "openai_compatible",
            "base_url": "https://openrouter.ai/api/v1",
            "auth_modes": ["api_key", "oauth_pkce"],
            "model_discovery": True,
            "category": "cloud",
            "docs_url": None,
            "note": None,
        }
    ]
    page.route("**/v1/ai/providers", lambda route: _json(route, providers))
    page.route("**/v1/settings/status", lambda route: _json(route, _data_status()))

    page.goto(f"{BASE_URL}/settings?lang=en", wait_until="networkidle")
    page.locator("#settings-provider").select_option("openrouter")
    page.locator("#settings-model").fill("example/model")
    page.locator("#settings-api-key").fill("secret-test-key")
    page.locator("#ai-settings-form").get_by_role("button", name="Save settings").click()

    assert page.evaluate("sessionStorage.getItem('yowayowa.ai.key.v2')") == "secret-test-key"
    page.locator("#clear-ai-api-key").click()

    expect(page.locator("#settings-api-key")).to_have_value("")
    expect(page.locator("#settings-status")).to_have_text("API key cleared.")
    assert page.evaluate("sessionStorage.getItem('yowayowa.ai.key.v2')") is None
    legacy = page.evaluate("JSON.parse(sessionStorage.getItem('yowayowa.ai.byok.session'))")
    assert legacy["apiKey"] == ""
