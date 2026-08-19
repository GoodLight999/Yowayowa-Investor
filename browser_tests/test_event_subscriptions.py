import json
from urllib.parse import urlparse

from playwright.sync_api import Page, Route, expect

BASE_URL = "http://127.0.0.1:8000"


def test_event_subscription_inbox_workflow(page: Page) -> None:
    subscriptions: list[dict[str, object]] = []
    inbox: list[dict[str, object]] = []
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    provenance = {
        "provider": "fixture",
        "source": "Fixture ticker calendar",
        "source_url": None,
        "license_class": "personal_only",
        "retrieved_at": "2026-08-13T12:00:00Z",
        "as_of": "2026-08-13T12:00:00Z",
        "notes": [],
    }

    def handler(route: Route) -> None:
        path = urlparse(route.request.url).path
        method = route.request.method
        status = 200
        if path == "/v1/alerts" and method == "GET":
            body: object = []
        elif path == "/v1/watchlists":
            body = [
                {
                    "id": 1,
                    "name": "Main",
                    "symbols": ["RKLB"],
                    "created_at": "2026-08-13T00:00:00Z",
                    "updated_at": "2026-08-13T00:00:00Z",
                }
            ]
        elif path == "/v1/portfolios":
            body = []
        elif path == "/v1/event-subscriptions" and method == "GET":
            body = subscriptions
        elif path == "/v1/event-subscriptions" and method == "POST":
            item = {
                "id": 3,
                "scope": "all",
                "scope_id": None,
                "event_types": ["earnings", "dividend"],
                "lead_days": 7,
                "enabled": True,
                "last_checked_at": None,
                "created_at": "2026-08-13T12:00:00Z",
            }
            subscriptions[:] = [item]
            body = item
            status = 201
        elif path == "/v1/event-inbox" and method == "GET":
            body = inbox
        elif path == "/v1/alerts/evaluate" and method == "POST":
            body = {
                "alerts": [],
                "provenance": provenance,
                "evaluated_at": "2026-08-13T12:00:00Z",
            }
        elif path == "/v1/event-subscriptions/evaluate" and method == "POST":
            item = {
                "id": 7,
                "subscription_id": 3,
                "event_type": "earnings",
                "subtype": "earnings",
                "symbol": "RKLB",
                "title": "Earnings date",
                "starts_at": "2026-08-16T12:00:00Z",
                "ends_at": None,
                "created_at": "2026-08-13T12:00:00Z",
                "acknowledged_at": None,
            }
            inbox[:] = [item]
            body = {
                "subscriptions": subscriptions,
                "inbox": inbox,
                "unavailable_symbols": [],
                "provenance": provenance,
                "evaluated_at": "2026-08-13T12:00:00Z",
            }
        elif path == "/v1/event-inbox/7/ack" and method == "POST":
            item = {**inbox[0], "acknowledged_at": "2026-08-13T12:01:00Z"}
            inbox.clear()
            body = item
        else:
            route.fallback()
            return
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

    page.route("**/v1/**", handler)
    response = page.goto(f"{BASE_URL}/alerts?lang=en", wait_until="networkidle")
    assert response is not None and response.ok

    expect(page.get_by_role("heading", name="New event subscription")).to_be_visible()
    page.get_by_role("button", name="Subscribe").click()
    expect(page.locator("#event-message")).to_contain_text("Event subscription saved")
    expect(page.locator("#event-subscriptions")).to_contain_text("Earnings")

    page.get_by_role("button", name="Check now").click()
    expect(page.locator("#event-inbox")).to_contain_text("RKLB")
    expect(page.locator("#event-inbox")).to_contain_text("Earnings date")
    expect(page.locator("#event-inbox-count")).to_have_text("1")

    page.get_by_role("button", name="Acknowledge").click()
    expect(page.locator("#event-inbox")).to_contain_text("No unread event notifications")
    expect(page.locator("#event-inbox-count")).to_have_text("0")
    assert page_errors == []
