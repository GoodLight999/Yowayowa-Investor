from playwright.sync_api import Page, expect

BASE_URL = "http://127.0.0.1:8000"


def test_builtin_strategy_hands_control_to_ai_without_manual_selection(page: Page) -> None:
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    response = page.goto(f"{BASE_URL}/discover?lang=ja", wait_until="networkidle")
    assert response is not None and response.ok

    preset = page.locator("#discover-preset")
    preset.select_option("builtin:kiyohara_global_value_growth")

    ai_button = page.locator("#discover-ai")
    expect(ai_button).to_be_enabled()
    expect(page.locator("#discover-region")).to_have_value("jp")

    ai_button.click()
    page.wait_for_url("**/ai?*")

    assert "strategy=kiyohara_global_value_growth" in page.url
    assert "region=jp" in page.url
    expect(page.locator("#ai-prompt")).to_contain_text("解釈可能な研究優先度")
    assert page_errors == []
