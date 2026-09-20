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
                    "prompt": 'YOWAYOWA DATA PACKET JSON:\n{"symbol":"TEST"}',
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
        'YOWAYOWA DATA PACKET JSON:\n{"symbol":"TEST"}'
    )
    expect(page.locator("#ai-prompt-packet-meta")).to_contain_text("get_fundamentals")



def test_hosted_codex_device_login_stays_in_browser(page: Page) -> None:
    def codex_status(route: Route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "enabled": True,
                    "installed": True,
                    "authenticated": False,
                    "version": None,
                    "auth_summary": None,
                    "login_command": "codex login",
                    "reason": "ChatGPT login is checked per browser session.",
                    "mode": "hosted_bridge",
                    "plan_type": None,
                    "email": None,
                    "credential": None,
                }
            ),
        )

    def device_auth(route: Route) -> None:
        body = "\n".join(
            [
                json.dumps(
                    {
                        "type": "instructions",
                        "login_id": "login-1",
                        "verification_url": "about:blank",
                        "user_code": "ABCD-1234",
                    }
                ),
                json.dumps(
                    {
                        "type": "complete",
                        "credential": "sealed-browser-credential",
                        "plan_type": "plus",
                        "email": "user@example.invalid",
                    }
                ),
                "",
            ]
        )
        route.fulfill(
            status=200,
            content_type="application/x-ndjson",
            body=body,
        )

    page.route("**/v1/ai/codex/status", codex_status)
    page.route("**/v1/ai/codex/device-auth", device_auth)
    page.goto(f"{BASE_URL}/settings?lang=en", wait_until="networkidle")
    page.locator("#settings-provider").select_option("codex")

    with page.expect_popup():
        page.locator("#codex-chatgpt-login").click()

    expect(page.locator("#codex-auth-status")).to_contain_text("Authenticated with ChatGPT")
    credential = page.evaluate(
        "() => localStorage.getItem('yowayowa.codex.credential.v1')"
    )
    assert credential == "sealed-browser-credential"


def test_hosted_codex_chat_sends_and_refreshes_sealed_credential(page: Page) -> None:
    requests: list[dict[str, object]] = []

    page.add_init_script(
        """
        localStorage.setItem('yowayowa.ai.settings.v2', JSON.stringify({
          providerId: 'codex',
          adapter: 'codex_cli',
          model: 'default',
          baseUrl: '',
          contextualEnabled: true
        }));
        localStorage.setItem('yowayowa.codex.credential.v1', 'sealed-one');
        localStorage.setItem('yowayowa.ai.enabled-providers.v1', JSON.stringify(['codex']));
        """
    )

    page.route(
        "**/v1/ai/codex/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "enabled": True,
                    "installed": True,
                    "authenticated": False,
                    "mode": "hosted_bridge",
                    "login_command": "codex login",
                }
            ),
        ),
    )
    page.route(
        "**/v1/ai/codex/session-status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "enabled": True,
                    "installed": True,
                    "authenticated": True,
                    "mode": "hosted_bridge",
                    "plan_type": "plus",
                    "credential": "sealed-two",
                    "login_command": "codex login",
                }
            ),
        ),
    )

    def chat(route: Route) -> None:
        requests.append(route.request.post_data_json)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "answer": "Hosted Codex works.",
                    "provider": "codex_cli",
                    "model": "default",
                    "tool_trace": [],
                    "proposed_operations": [],
                    "provider_credential": "sealed-three",
                }
            ),
        )

    page.route("**/v1/ai/chat", chat)
    page.goto(f"{BASE_URL}/ai?lang=en", wait_until="networkidle")
    page.locator("#ai-prompt").fill("Analyze this")
    page.locator("#ai-send").click()

    expect(page.locator("#ai-messages")).to_contain_text("Hosted Codex works.")
    assert requests
    assert requests[0]["provider"]["provider"] == "codex_cli"
    assert requests[0]["provider"]["credential"] == "sealed-two"
    credential = page.evaluate(
        "() => localStorage.getItem('yowayowa.codex.credential.v1')"
    )
    assert credential == "sealed-three"
