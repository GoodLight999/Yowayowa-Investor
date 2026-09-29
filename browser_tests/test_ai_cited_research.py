import json
import os
from urllib.parse import urlsplit

from playwright.sync_api import Page, Route, expect

BASE_URL = os.environ.get("YOWAYOWA_BROWSER_BASE_URL", "http://127.0.0.1:8000")


def _stub_only_local_traffic(page: Page) -> None:
    def allow_local_application(route: Route) -> None:
        url = urlsplit(route.request.url)
        if url.scheme == "http" and url.netloc == urlsplit(BASE_URL).netloc:
            route.continue_()
        else:
            route.abort()

    page.route("**/*", allow_local_application)

    def stub_unmatched_api(route: Route) -> None:
        route.fulfill(
            status=404,
            content_type="application/json",
            body='{"detail":"stubbed"}',
        )

    page.route("**/v1/**", stub_unmatched_api)
    page.route(
        "**/v1/health",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"mode": "personal"}),
        ),
    )


def test_ai_cited_research_renders_evidence_and_avoids_general_chat(page: Page) -> None:
    requests: list[dict[str, object]] = []
    chat_calls: list[str] = []
    _stub_only_local_traffic(page)

    page.route(
        "**/v1/ai/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "configured": {"openai_compatible": True, "anthropic": False},
                    "tools": [],
                }
            ),
        ),
    )

    def research_ask(route: Route) -> None:
        requests.append(route.request.post_data_json)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "question": "6758の業績は?",
                    "answer": "売上は未取得です。[F1]",
                    "citations": [
                        {
                            "provider": "fixture-provider",
                            "source": "Fixture filing",
                            "source_url": "https://example.invalid/filing",
                            "retrieved_at": "2026-09-29T00:00:00Z",
                            "as_of": "2026-09-28",
                            "kind": "edinet_filing",
                            "code_or_series": "S100TEST",
                        },
                        {
                            "provider": "untrusted",
                            "source_url": "javascript:alert(1)",
                            "kind": "unsafe_link",
                        },
                    ],
                    "tool_trace": [
                        {
                            "tool": "edinet_daily_lookup",
                            "arguments": {"codes": ["6758"]},
                            "matched": 1,
                        }
                    ],
                    "coverage": {"missing_inputs": ["financials: 未取得"]},
                    "provider": "openai_compatible",
                    "model": "fixture-model",
                    "facts": [
                        {
                            "id": "F1",
                            "kind": "edinet_filing",
                            "statement": "Test evidence <script>not-run</script>",
                            "provider": "edinet-v2",
                            "source_url": "https://example.invalid/filing",
                            "as_of": "2026-09-28",
                        }
                    ],
                    "inferences": [],
                    "missing_inputs": ["financials: 未取得"],
                }
            ),
        )

    def general_chat(route: Route) -> None:
        chat_calls.append(route.request.url)
        route.fulfill(status=500, body="unexpected general chat call")

    page.route("**/v1/research/ask", research_ask)
    page.route("**/v1/ai/chat", general_chat)
    page.goto(f"{BASE_URL}/ai?lang=en", wait_until="networkidle")

    page.locator("#ai-mode").select_option("cited")
    page.locator("#ai-prompt").fill("6758の業績は?")
    page.locator("#ai-send").click()

    evidence = page.locator(".ai-research-evidence")
    expect(evidence).to_contain_text("Test evidence <script>not-run</script>")
    expect(evidence).to_contain_text("Fixture filing")
    expect(evidence).to_contain_text("financials: 未取得")
    expect(evidence).to_contain_text("edinet_daily_lookup")
    expect(evidence).to_contain_text("matched: 1")
    expect(evidence.locator('a[href="https://example.invalid/filing"]')).to_have_count(1)
    expect(evidence.locator('a[href^="javascript:"]')).to_have_count(0)
    expect(evidence.locator("script")).to_have_count(0)
    assert len(requests) == 1
    assert requests[0]["question"] == "6758の業績は?"
    assert requests[0]["provider"] is None
    assert chat_calls == []


def test_ai_cited_research_rejects_overlong_question_before_request(page: Page) -> None:
    research_calls: list[str] = []
    _stub_only_local_traffic(page)
    page.route(
        "**/v1/ai/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"configured": {"openai_compatible": True}, "tools": []}),
        ),
    )

    def research_ask(route: Route) -> None:
        research_calls.append(route.request.url)
        route.fulfill(status=500, body="unexpected research request")

    page.route("**/v1/research/ask", research_ask)
    page.goto(f"{BASE_URL}/ai?lang=en", wait_until="networkidle")
    page.locator("#ai-mode").select_option("cited")
    page.locator("#ai-prompt").fill("x" * 2001)
    page.locator("#ai-send").click()

    expect(page.locator("#ai-status")).to_contain_text("2,000 characters or fewer")
    assert research_calls == []


def test_ai_general_mode_keeps_the_existing_chat_endpoint(page: Page) -> None:
    chat_requests: list[dict[str, object]] = []
    research_calls: list[str] = []
    _stub_only_local_traffic(page)
    page.route(
        "**/v1/ai/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"configured": {"openai_compatible": True}, "tools": []}),
        ),
    )

    def general_chat(route: Route) -> None:
        chat_requests.append(route.request.post_data_json)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "answer": "General chat answer",
                    "provider": "openai_compatible",
                    "model": "fixture-model",
                    "tool_trace": [],
                    "proposed_operations": [],
                }
            ),
        )

    def research_ask(route: Route) -> None:
        research_calls.append(route.request.url)
        route.fulfill(status=500, body="unexpected cited research request")

    page.route("**/v1/ai/chat", general_chat)
    page.route("**/v1/research/ask", research_ask)
    page.goto(f"{BASE_URL}/ai?lang=en", wait_until="networkidle")
    expect(page.locator("#ai-mode")).to_have_value("agent")
    page.locator("#ai-prompt").fill("Explain the evidence")
    page.locator("#ai-send").click()

    expect(page.locator("#ai-messages")).to_contain_text("General chat answer")
    expect(page.locator(".ai-research-evidence")).to_have_count(0)
    assert len(chat_requests) == 1
    assert chat_requests[0]["messages"][-1] == {
        "role": "user",
        "content": "Explain the evidence",
    }
    assert research_calls == []
