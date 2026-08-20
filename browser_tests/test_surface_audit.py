from playwright.sync_api import Page, expect

BASE_URL = "http://127.0.0.1:8000"
SURFACE_ROUTES = (
    "/",
    "/markets",
    "/discover",
    "/portfolio",
    "/compare",
    "/screener",
    "/news",
    "/calendar",
    "/macro",
    "/alerts",
    "/ai",
    "/settings",
    "/charts",
    "/rates",
    "/institutional",
    "/edinet",
    "/licenses",
)


def test_surfaces_have_no_page_overflow_or_static_css_dependency(page: Page) -> None:
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    overflow_expression = (
        "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )
    for width in (1440, 390):
        page.set_viewport_size({"width": width, "height": 844})
        for locale in ("ja", "en"):
            for route in SURFACE_ROUTES:
                page.goto(f"{BASE_URL}{route}?lang={locale}", wait_until="domcontentloaded")
                expect(page.locator("html")).to_have_attribute("lang", locale)
                overflow = page.evaluate(overflow_expression)
                assert overflow is False, f"page overflow at {width}px: {route} ({locale})"
                assert page.locator('link[rel="stylesheet"][href*="/static/"]').count() == 0
    assert errors == []


def test_generic_surfaces_do_not_leak_owner_specific_ticker_examples(page: Page) -> None:
    for locale in ("ja", "en"):
        for route in ("/", "/news", "/settings"):
            page.goto(f"{BASE_URL}{route}?lang={locale}", wait_until="domcontentloaded")
            assert "RKLB" not in page.content()
            assert "Rocket Lab" not in page.content()

    page.goto(f"{BASE_URL}/?lang=en", wait_until="domcontentloaded")
    expect(page.locator("#search-input")).to_have_attribute("placeholder", "AAPL / Apple / 7203.T / Toyota")
    expect(page.locator("#operator-input")).to_have_attribute("placeholder", "Example: compare AAPL and MSFT")
    expect(page.locator("#watchlist-symbol")).to_have_attribute("placeholder", "AAPL")

    page.goto(f"{BASE_URL}/news?lang=en", wait_until="domcontentloaded")
    expect(page.locator("#news-query")).to_have_attribute("placeholder", "AAPL / Apple / 7203.T / Toyota")
