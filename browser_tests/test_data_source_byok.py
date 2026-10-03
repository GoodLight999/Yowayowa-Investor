from __future__ import annotations

import json
from urllib.parse import urlparse

from playwright.sync_api import Page, Route, expect

BASE_URL = "http://127.0.0.1:8000"


def _json(route: Route, payload: object) -> None:
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def test_settings_browser_byok_is_scoped_to_matching_data_source(page: Page) -> None:
    captured: dict[str, dict[str, str]] = {}

    def capture(name: str):
        def handler(route: Route) -> None:
            captured[name] = route.request.headers
            _json(route, {})

        return handler

    page.route("**/v1/macro/fred/search*", capture("fred"))
    page.route("**/v1/instruments/search*", capture("instruments"))
    page.goto(f"{BASE_URL}/settings?lang=en", wait_until="networkidle")

    panel = page.locator('[data-browser-source="fred"]')
    expect(panel).to_be_visible()
    panel.locator("summary").click()
    panel.locator("input").fill("browser-fred-secret")
    panel.locator('button[data-action="save"]').click()
    expect(panel.locator('[data-role="status"]')).to_contain_text("Ready · browser tab")
    expect(page.locator('[data-source="fred"]')).to_contain_text("Ready · browser tab")

    stored = page.evaluate("sessionStorage.getItem('yowayowa.datasource.fred.key.v1')")
    assert stored == "browser-fred-secret"

    page.evaluate(
        """async () => {
          await fetch('/v1/macro/fred/search?q=inflation').then(response => response.json());
          await fetch('/v1/instruments/search?q=AAPL').then(response => response.json());
        }"""
    )

    assert captured["fred"].get("x-yowayowa-fred-key") == "browser-fred-secret"
    assert "x-yowayowa-fred-key" not in captured["instruments"]
    assert "x-yowayowa-edinet-key" not in captured["instruments"]
    assert "x-yowayowa-estat-key" not in captured["instruments"]
    assert "x-yowayowa-bea-key" not in captured["instruments"]
    assert "x-yowayowa-bls-key" not in captured["instruments"]


def test_each_macro_browser_key_uses_its_own_header(page: Page) -> None:
    routes = {
        "estat": "/v1/macro/estat/tables?q=cpi",
        "fred": "/v1/macro/fred/search?q=inflation",
        "bea": "/v1/macro/bea/nipa/catalog",
        "bls": "/v1/macro/bls/catalog",
    }
    expected_paths = {
        "/v1/macro/estat/tables": "estat",
        "/v1/macro/fred/search": "fred",
        "/v1/macro/bea/nipa/catalog": "bea",
        "/v1/macro/bls/catalog": "bls",
    }
    expected_headers = {
        "estat": "x-yowayowa-estat-key",
        "fred": "x-yowayowa-fred-key",
        "bea": "x-yowayowa-bea-key",
        "bls": "x-yowayowa-bls-key",
    }
    captured: dict[str, dict[str, str]] = {}

    def macro_handler(route: Route) -> None:
        source = expected_paths.get(urlparse(route.request.url).path)
        if source is None:
            route.fallback()
            return
        captured[source] = route.request.headers
        _json(route, {})

    page.route("**/v1/macro/**", macro_handler)
    page.goto(f"{BASE_URL}/settings?lang=en", wait_until="networkidle")
    for source in routes:
        panel = page.locator(f'[data-browser-source="{source}"]')
        expect(panel).to_be_visible()
        panel.locator("summary").click()
        panel.locator("input").fill(f"{source}-secret")
        panel.locator('button[data-action="save"]').click()

    for path in routes.values():
        page.evaluate(
            "path => fetch(path).then(response => response.json())",
            path,
        )

    assert set(captured) == set(routes)
    for source, header in expected_headers.items():
        assert captured[source].get(header) == f"{source}-secret"
        for other_header in expected_headers.values():
            if other_header != header:
                assert other_header not in captured[source]
