import json

from playwright.sync_api import Page, Route, expect

BASE_URL = "http://127.0.0.1:8000"


def _json(route: Route, payload: object) -> None:
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def _data_status(*, allow_unlisted_ai_endpoints: bool = False) -> dict[str, bool]:
    return {
        "sec": True,
        "yahoo_personal": True,
        "edinet": False,
        "estat": False,
        "fred": False,
        "bea": False,
        "allow_unlisted_ai_endpoints": allow_unlisted_ai_endpoints,
    }


def test_edinet_browser_key_updates_data_source_status(page: Page) -> None:
    page.route("**/v1/ai/providers", lambda route: _json(route, []))
    page.route("**/v1/settings/status", lambda route: _json(route, _data_status()))

    page.goto(f"{BASE_URL}/settings?lang=ja", wait_until="networkidle")

    edinet_status = page.locator('[data-source="edinet"] .ux-note')
    expect(edinet_status).to_have_text("未設定")

    page.locator("#edinet-browser-key").fill("browser-edinet-key")
    page.locator("#save-edinet-browser-key").click()
    expect(page.locator("#edinet-browser-key-status")).to_have_text("このタブで利用可能")
    expect(edinet_status).to_have_text("このタブで利用可能")

    page.reload(wait_until="networkidle")
    expect(page.locator('[data-source="edinet"] .ux-note')).to_have_text("このタブで利用可能")
    expect(page.locator("#edinet-browser-key")).to_have_value("browser-edinet-key")

    page.locator("#clear-edinet-browser-key").click()
    expect(page.locator("#edinet-browser-key-status")).to_have_text("未設定")
    expect(page.locator('[data-source="edinet"] .ux-note')).to_have_text("未設定")


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


def test_hosted_settings_disable_local_and_custom_ai_endpoints(page: Page) -> None:
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
        },
        {
            "id": "ollama",
            "label": "Ollama",
            "adapter": "openai_compatible",
            "base_url": "http://127.0.0.1:11434/v1",
            "auth_modes": ["none"],
            "model_discovery": True,
            "category": "local",
            "docs_url": None,
            "note": "Local endpoint",
        },
        {
            "id": "custom",
            "label": "Custom OpenAI-compatible",
            "adapter": "openai_compatible",
            "base_url": "https://example.invalid/v1",
            "auth_modes": ["api_key"],
            "model_discovery": True,
            "category": "custom",
            "docs_url": None,
            "note": "Custom endpoint",
        },
    ]
    page.route("**/v1/ai/providers", lambda route: _json(route, providers))
    page.route("**/v1/settings/status", lambda route: _json(route, _data_status()))

    page.goto(f"{BASE_URL}/settings?lang=en", wait_until="networkidle")
    manager = page.locator(".provider-manager")
    manager.locator("summary").click()

    expect(manager.locator('input[value="openrouter"]')).to_be_enabled()
    expect(manager.locator('input[value="ollama"]')).to_be_disabled()
    expect(manager.locator('input[value="custom"]')).to_be_disabled()
    expect(manager).to_contain_text("Ollama · self-host only")
    expect(manager).to_contain_text("Custom OpenAI-compatible · self-host only")
    assert page.locator('#settings-provider option[value="ollama"]').count() == 0
    assert page.locator('#settings-provider option[value="custom"]').count() == 0


def test_self_host_settings_allow_local_ai_provider(page: Page) -> None:
    providers = [
        {
            "id": "ollama",
            "label": "Ollama",
            "adapter": "openai_compatible",
            "base_url": "http://127.0.0.1:11434/v1",
            "auth_modes": ["none"],
            "model_discovery": True,
            "category": "local",
            "docs_url": None,
            "note": "Local endpoint",
        }
    ]
    page.route("**/v1/ai/providers", lambda route: _json(route, providers))
    page.route(
        "**/v1/settings/status",
        lambda route: _json(route, _data_status(allow_unlisted_ai_endpoints=True)),
    )

    page.goto(f"{BASE_URL}/settings?lang=en", wait_until="networkidle")
    manager = page.locator(".provider-manager")
    manager.locator("summary").click()
    checkbox = manager.locator('input[value="ollama"]')
    expect(checkbox).to_be_enabled()
    checkbox.check()
    expect(page.locator('#settings-provider option[value="ollama"]')).to_have_count(1)
