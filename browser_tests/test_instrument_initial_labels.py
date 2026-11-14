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


def test_instrument_first_paint_guidance_matches_locale(page: Page) -> None:
    response = page.request.get(f"{BASE_URL}/instrument/AAPL?lang=en")
    assert response.ok
    html = response.text()
    assert "average closing price over 20 periods" in html
    assert "Shows a volatility range around the 20-period moving average" in html
    assert "Build Bull / Base / Bear scenarios for AAPL over the next 12 months." in html
    assert "FY = annual results; Q = quarterly results." in html

    response = page.request.get(f"{BASE_URL}/instrument/AAPL?lang=ja")
    assert response.ok
    html = response.text()
    assert "20日単純移動平均" in html
    assert "20日平均の周辺に値動きの幅を表示" in html
    assert "AAPL の今後12か月を Bull / Base / Bear の3シナリオで整理して。" in html
    assert "FY = 年次決算、Q = 四半期決算。" in html
