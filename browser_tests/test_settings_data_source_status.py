import json

from playwright.sync_api import Page, Route, expect

BASE_URL = "http://127.0.0.1:8000"


def _json(route: Route, payload: object) -> None:
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def test_edinet_browser_key_updates_data_source_status(page: Page) -> None:
    status = {
        "sec": True,
        "yahoo_personal": True,
        "edinet": False,
        "estat": False,
        "fred": False,
        "bea": False,
    }
    page.route("**/v1/ai/providers", lambda route: _json(route, []))
    page.route("**/v1/settings/status", lambda route: _json(route, status))

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
