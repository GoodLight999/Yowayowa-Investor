import json

from playwright.sync_api import Page, Route, expect

BASE_URL = "http://127.0.0.1:8000"


def _json(route: Route, payload: object) -> None:
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def _status(configured: bool = False) -> dict[str, object]:
    return {
        "configured": {
            "openai_compatible": configured,
            "anthropic": False,
        },
        "tools": ["search_instruments", "company_fundamentals"],
        "byok_per_request": True,
        "keys_persisted_by_server": False,
    }


def test_ai_research_guides_to_settings_when_server_provider_is_missing(page: Page) -> None:
    chat_calls: list[str] = []
    page.route("**/v1/ai/status", lambda route: _json(route, _status(False)))

    def chat_handler(route: Route) -> None:
        chat_calls.append(route.request.url)
        _json(route, {"answer": "unexpected"})

    page.route("**/v1/ai/chat", chat_handler)
    page.goto(f"{BASE_URL}/ai?lang=en", wait_until="networkidle")

    send = page.locator("#ai-send")
    expect(send).to_be_disabled()
    setup_link = page.locator('#ai-status a[href="/settings"]')
    expect(setup_link).to_be_visible()
    expect(setup_link).to_contain_text("Open Settings")

    # Even an artificial submit must be preflighted instead of turning a known
    # missing configuration into a 424 from the backend.
    page.locator("#ai-prompt").fill("Analyze AAPL")
    page.locator("#ai-form").evaluate("form => form.requestSubmit()")
    expect(setup_link).to_be_visible()
    assert chat_calls == []


def test_ai_research_uses_settings_byok_when_server_is_missing(page: Page) -> None:
    page.route("**/v1/ai/status", lambda route: _json(route, _status(False)))
    captured: list[dict[str, object]] = []

    def chat_handler(route: Route) -> None:
        captured.append(route.request.post_data_json)
        _json(
            route,
            {
                "answer": "Configured answer",
                "provider": "openai_compatible",
                "model": "example/model",
                "tool_trace": [],
                "proposed_operations": [],
            },
        )

    page.route("**/v1/ai/chat", chat_handler)
    page.goto(f"{BASE_URL}/ai?lang=en", wait_until="domcontentloaded")
    page.evaluate(
        """
        localStorage.setItem('yowayowa.ai.settings.v2', JSON.stringify({
          providerId: 'openrouter',
          adapter: 'openai_compatible',
          model: 'example/model',
          baseUrl: 'https://openrouter.ai/api/v1',
          contextualEnabled: true
        }));
        sessionStorage.setItem('yowayowa.ai.key.v2', 'browser-key');
        """
    )
    page.reload(wait_until="networkidle")

    expect(page.locator("#ai-current-provider")).to_contain_text("openrouter · example/model")
    expect(page.locator("#ai-send")).to_be_enabled()
    page.locator("#ai-prompt").fill("Analyze AAPL")
    page.locator("#ai-send").click()
    expect(page.locator("#ai-messages")).to_contain_text("Configured answer")
    assert len(captured) == 1
    provider = captured[0]["provider"]
    assert provider["model"] == "example/model"
    assert provider["api_key"] == "browser-key"


def test_contextual_ai_missing_provider_links_to_settings_without_chat(page: Page) -> None:
    chat_calls: list[str] = []
    page.route("**/v1/ai/status", lambda route: _json(route, _status(False)))
    page.route("**/v1/watchlists", lambda route: _json(route, []))

    def chat_handler(route: Route) -> None:
        chat_calls.append(route.request.url)
        _json(route, {"answer": "unexpected"})

    page.route("**/v1/ai/chat", chat_handler)
    page.goto(f"{BASE_URL}/?lang=en", wait_until="networkidle")

    button = page.locator(".section-ai-button").first
    expect(button).to_be_visible()
    button.click()
    page.locator("#context-ai-prompt").fill("Explain this")
    page.locator("#context-ai-send").click()

    answer = page.locator("#context-ai-answer")
    expect(answer.locator('a[href="/settings"]')).to_be_visible()
    expect(answer).to_contain_text("Open Settings")
    assert chat_calls == []


def test_disabling_contextual_ai_removes_buttons_on_current_page(page: Page) -> None:
    page.route("**/v1/watchlists", lambda route: _json(route, []))
    page.goto(f"{BASE_URL}/?lang=en", wait_until="networkidle")
    expect(page.locator(".section-ai-button").first).to_be_visible()

    page.evaluate(
        """
        localStorage.setItem('yowayowa.ai.settings.v2', JSON.stringify({
          providerId: 'server', contextualEnabled: false
        }));
        window.dispatchEvent(new CustomEvent('yowayowa:settings-changed'));
        """
    )
    expect(page.locator(".section-ai-button")).to_have_count(0)
