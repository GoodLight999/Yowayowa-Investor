from playwright.sync_api import Page

BASE_URL = "http://127.0.0.1:8000"


def test_instrument_first_paint_uses_provider_neutral_valuation_basis(page: Page) -> None:
    response = page.request.get(f"{BASE_URL}/instrument/AAPL?lang=ja")
    assert response.ok
    html = response.text()
    assert 'id="valuation-basis" class="pill">最新年次財務</span>' in html
    assert 'id="valuation-basis" class="pill">最新年次SEC財務</span>' not in html

    response = page.request.get(f"{BASE_URL}/instrument/AAPL?lang=en")
    assert response.ok
    html = response.text()
    assert 'id="valuation-basis" class="pill">Latest annual financials</span>' in html
    assert 'id="valuation-basis" class="pill">Latest annual SEC facts</span>' not in html
