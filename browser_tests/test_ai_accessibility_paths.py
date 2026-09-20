import json

from playwright.sync_api import Page, Route, expect

BASE_URL = "http://127.0.0.1:8000"


def test_settings_exposes_codex_chatgpt_subscription_provider(page: Page) -> None:
    page.goto(f"{BASE_URL}/settings?lang=en", wait_until="networkidle")

    provider = page.locator("#settings-provider")
    expect(provider.locator('option[value="codex"]')).to_have_count(1)
    provider.select_option("codex")

    expect(page.locator("#codex-chatgpt-login")).to_be_visible()
    expect(page.locator("#settings-api-key")).to_be_disabled()
    expect(page.locator("#settings-base-url")).to_be_disabled()
    expect(page.locator("#settings-model")).to_have_value("default")


def test_ai_can_generate_copyable_external_research_packet(page: Page) -> None:
    def prompt_packet(route: Route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "prompt": "YOWAYOWA DATA PACKET JSON:\n{\"symbol\":\"TEST\"}",
                    "included_tools": ["get_quotes", "get_fundamentals"],
                    "generated_at": "2026-09-21T00:00:00+00:00",
                    "characters": 49,
                }
            ),
        )

    page.route("**/v1/ai/prompt-packet", prompt_packet)
    page.goto(f"{BASE_URL}/ai?lang=en&symbol=TEST", wait_until="networkidle")
    page.locator("#ai-export-prompt").click()

    panel = page.locator("#ai-prompt-packet-panel")
    expect(panel).to_be_visible()
    expect(page.locator("#ai-prompt-packet")).to_have_value(
        "YOWAYOWA DATA PACKET JSON:\n{\"symbol\":\"TEST\"}"
    )
    expect(page.locator("#ai-prompt-packet-meta")).to_contain_text("get_fundamentals")
