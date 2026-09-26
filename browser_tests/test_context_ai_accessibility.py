from playwright.sync_api import Page, expect

BASE_URL = "http://127.0.0.1:8000"


def test_context_ai_drawer_is_inert_when_closed_and_restores_focus(page: Page) -> None:
    response = page.goto(f"{BASE_URL}/?lang=en", wait_until="networkidle")
    assert response is not None and response.ok

    drawer = page.locator("#context-ai-drawer")
    expect(drawer).to_have_attribute("aria-hidden", "true")
    assert drawer.evaluate("element => element.inert") is True

    opener = page.locator(".section-ai-button").first
    expect(opener).to_be_visible()
    opener.focus()
    opener.click()

    expect(drawer).to_have_class("context-ai-drawer is-open")
    expect(drawer).to_have_attribute("aria-hidden", "false")
    assert drawer.evaluate("element => element.inert") is False
    expect(page.locator("#context-ai-prompt")).to_be_focused()

    page.keyboard.press("Escape")
    expect(drawer).to_have_attribute("aria-hidden", "true")
    assert drawer.evaluate("element => element.inert") is True
    expect(opener).to_be_focused()
