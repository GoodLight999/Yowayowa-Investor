import re

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
EXPECTED_SHARED_STYLE_NAMES = {
    "styles.css",
    "expansion.css",
    "ux.css",
    "product.css",
    "pages.css",
    "interface.css",
}
FINGERPRINTED_STYLE_PATH = re.compile(r"^/assets/static/[0-9a-f]{16}/[^/]+\.css$")


def test_surfaces_have_no_page_overflow_and_load_shared_css(page: Page) -> None:
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    overflow_expression = (
        "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )
    stylesheet_expression = """
        [...document.styleSheets]
          .map(sheet => sheet.href ? new URL(sheet.href).pathname : null)
          .filter(Boolean)
    """
    for width in (1440, 390):
        page.set_viewport_size({"width": width, "height": 844})
        for locale in ("ja", "en"):
            for route in SURFACE_ROUTES:
                page.goto(f"{BASE_URL}{route}?lang={locale}", wait_until="domcontentloaded")
                expect(page.locator("html")).to_have_attribute("lang", locale)
                overflow = page.evaluate(overflow_expression)
                assert overflow is False, f"page overflow at {width}px: {route} ({locale})"
                loaded_styles = set(page.evaluate(stylesheet_expression))
                loaded_names = {path.rsplit("/", 1)[-1] for path in loaded_styles}
                assert loaded_names >= EXPECTED_SHARED_STYLE_NAMES, (
                    route,
                    locale,
                    loaded_styles,
                )
                shared_paths = {
                    path
                    for path in loaded_styles
                    if path.rsplit("/", 1)[-1] in EXPECTED_SHARED_STYLE_NAMES
                }
                assert all(FINGERPRINTED_STYLE_PATH.fullmatch(path) for path in shared_paths)
    assert errors == []


def test_core_interface_does_not_shrink_primary_text_into_microcopy(page: Page) -> None:
    for width in (1440, 390):
        page.set_viewport_size({"width": width, "height": 844})
        page.goto(f"{BASE_URL}/?lang=ja", wait_until="domcontentloaded")
        sizes = page.evaluate(
            """() => {
                const px = selector => {
                    const node = document.querySelector(selector);
                    return parseFloat(getComputedStyle(node).fontSize);
                };
                return {
                    body: px('body'),
                    nav: px('.nav a'),
                    search: px('#search-input'),
                    searchButton: px('#global-search button'),
                    subtitle: px('.ux-subtitle'),
                };
            }"""
        )
        assert sizes["body"] >= 14, sizes
        assert sizes["nav"] >= 11, sizes
        assert sizes["search"] >= 14, sizes
        assert sizes["searchButton"] >= 12, sizes
        assert sizes["subtitle"] >= 12, sizes


def test_generic_surfaces_do_not_leak_owner_specific_ticker_examples(page: Page) -> None:
    for locale in ("ja", "en"):
        for route in ("/", "/news", "/settings"):
            page.goto(f"{BASE_URL}{route}?lang={locale}", wait_until="domcontentloaded")
            assert "RKLB" not in page.content()
            assert "Rocket Lab" not in page.content()

    page.goto(f"{BASE_URL}/?lang=en", wait_until="domcontentloaded")
    expect(page.locator("#search-input")).to_have_attribute(
        "placeholder", "AAPL / Apple / 7203.T / Toyota"
    )
    expect(page.locator("#operator-input")).to_have_attribute(
        "placeholder", "Example: compare AAPL and MSFT"
    )
    expect(page.locator("#watchlist-symbol")).to_have_attribute("placeholder", "AAPL")

    page.goto(f"{BASE_URL}/news?lang=en", wait_until="domcontentloaded")
    expect(page.locator("#news-query")).to_have_attribute(
        "placeholder", "AAPL / Apple / 7203.T / Toyota"
    )
