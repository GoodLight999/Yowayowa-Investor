from playwright.sync_api import Page, expect

BASE_URL = "http://127.0.0.1:8000"

PRIMARY_NAV_HREFS = (
    "/",
    "/discover",
    "/markets",
    "/macro",
    "/portfolio",
    "/ai",
    "/settings",
)

ADVANCED_PATHS = (
    "/compare",
    "/screener",
    "/charts",
    "/calendar",
    "/rates",
    "/alerts",
    "/institutional",
    "/edinet",
    "/news",
    "/licenses",
    "/docs",
)
APP_PATHS = PRIMARY_NAV_HREFS + tuple(path for path in ADVANCED_PATHS if path != "/docs")


def test_mobile_navigation_is_grouped_and_not_a_tool_dump(page: Page) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(BASE_URL, wait_until="networkidle")

    nav = page.get_by_role("navigation", name="Main navigation")
    expect(nav).to_be_visible()
    expect(nav.locator(".nav-group")).to_have_count(4)
    expect(nav.locator(".nav-group > a")).to_have_count(len(PRIMARY_NAV_HREFS))

    for href in PRIMARY_NAV_HREFS:
        expect(nav.locator(f'a[href="{href}"]')).to_have_count(1)
    for href in ADVANCED_PATHS:
        expect(nav.locator(f'a[href="{href}"]')).to_have_count(0)


def test_mobile_app_routes_have_no_page_level_horizontal_overflow(page: Page) -> None:
    page.set_viewport_size({"width": 390, "height": 844})

    for path in APP_PATHS:
        response = page.goto(f"{BASE_URL}{path}", wait_until="load")
        assert response is not None and response.ok, path
        overflow = page.evaluate(
            """() => ({
                viewport: window.innerWidth,
                html: document.documentElement.scrollWidth,
                body: document.body.scrollWidth,
            })"""
        )
        assert overflow["html"] <= overflow["viewport"] + 1, f"{path}: {overflow}"
        assert overflow["body"] <= overflow["viewport"] + 1, f"{path}: {overflow}"
