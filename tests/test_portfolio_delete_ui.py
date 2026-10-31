"""P5 R3 (UI): the portfolio page must offer a real delete path, not just create.

The API/CLI side is covered by ``tests/test_portfolio_delete.py``. This file
covers the remaining hole: the browser surface.

Contract pinned here (real served HTML and served script bundle, not source
paths, so a missing asset fingerprint also fails):

* the ``/portfolio`` page renders a danger-zone delete control that is disabled
  until a portfolio is selected, in both locales;
* the served ``portfolio.js`` bundle targets the currently selected portfolio,
  calls ``DELETE /v1/portfolios/{id}``, asks for confirmation first, treats a
  ``409`` as a second (force) confirmation instead of a silent failure, and
  reloads the list after a ``404``/success;
* ``app.js`` exposes the ``409`` detail so the force prompt can state how many
  holdings/history rows would be destroyed;
* every ``portfolio.delete*`` message exists in both catalogs and its
  placeholders match what ``portfolio.js`` passes.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from yowayowa.api.app import app
from yowayowa.web.i18n import translate

_PAGE_BUNDLE_PATTERN = re.compile(r'src="(/assets/page/[0-9a-f]{16}/([a-z_]+)\.js)"')
_SHARED_SCRIPT_PATTERN = re.compile(r'src="(/assets/static/[0-9a-f]{16}/(app\.js))"')
_I18N_KEY_PATTERN = re.compile(r"\bt\('([a-z0-9_]+(?:\.[a-z0-9_]+)*)'")

# key -> the keyword arguments portfolio.js passes when it calls translate()
_DELETE_KEY_CONTRACT: dict[str, dict[str, object]] = {
    "portfolio.danger_zone": {},
    "portfolio.delete": {},
    "portfolio.delete_note": {},
    "portfolio.delete_none": {},
    "portfolio.delete_target": {"name": "Core"},
    "portfolio.delete_confirm": {"name": "Core"},
    "portfolio.delete_force_confirm": {
        "name": "Core",
        "positions": 2,
        "snapshots": 1,
        "applied_executions": 3,
    },
    "portfolio.delete_force_confirm_generic": {"name": "Core"},
    "portfolio.delete_cancelled": {"name": "Core"},
    "portfolio.delete_missing": {"name": "Core"},
    "portfolio.delete_in_progress": {"name": "Core"},
    "portfolio.deleted": {"name": "Core"},
    "portfolio.deleted_forced": {
        "name": "Core",
        "positions": 2,
        "snapshots": 1,
        "applied_executions": 3,
    },
}

# Rendered by the Jinja template only; portfolio.js never calls translate() for them.
_TEMPLATE_ONLY_KEYS = frozenset({"portfolio.danger_zone", "portfolio.delete"})


def _served_bundle(client: TestClient, path: str) -> str:
    page = client.get(path)
    assert page.status_code == 200, path
    match = _PAGE_BUNDLE_PATTERN.search(page.text)
    assert match is not None, path
    bundle = client.get(match.group(1))
    assert bundle.status_code == 200
    return bundle.text


def test_portfolio_page_renders_a_delete_control_in_both_locales() -> None:
    with TestClient(app) as client:
        ja = client.get("/portfolio?lang=ja")
        en = client.get("/portfolio?lang=en")

    assert ja.status_code == 200
    assert en.status_code == 200

    for page in (ja, en):
        assert 'id="portfolio-delete"' in page.text
        assert 'id="portfolio-delete-note"' in page.text
        assert 'class="ghost portfolio-delete"' in page.text
        # Fail closed: nothing is deletable before a portfolio is selected.
        assert re.search(r'<button id="portfolio-delete"[^>]*disabled', page.text) is not None

    assert "ポートフォリオを削除" in ja.text
    assert "保有銘柄と評価履歴ごと削除" in ja.text
    assert "Delete portfolio" in en.text
    assert "holdings and valuation history" in en.text


def test_served_portfolio_bundle_deletes_the_selected_portfolio_safely() -> None:
    with TestClient(app) as client:
        bundle = _served_bundle(client, "/portfolio")

    # Targets the portfolio currently selected in the UI, not a hardcoded id.
    assert "async function removePortfolio()" in bundle
    assert "const portfolio = currentPortfolio();" in bundle
    assert "await api(`/v1/portfolios/${portfolio.id}`, { method: 'DELETE' });" in bundle
    # Confirmation before the destructive call, with the portfolio name.
    assert "window.confirm(t('portfolio.delete_confirm', { name: portfolio.name }))" in bundle
    # 409 is a second, explicit force confirmation - never a silent failure.
    assert "error.status !== 409" in bundle
    assert "danglingCounts(error.detail)" in bundle
    assert "/v1/portfolios/${portfolio.id}?force=true" in bundle
    assert "t('portfolio.delete_force_confirm'" in bundle
    # Declining the force prompt keeps the portfolio and says so.
    assert "t('portfolio.delete_cancelled', { name: portfolio.name })" in bundle
    # 404 means "already gone": say so and reload the list.
    assert "t('portfolio.delete_missing', { name: portfolio.name })" in bundle
    # Success reloads the list so an empty selector keeps its own message.
    assert "await loadPortfolios();" in bundle
    assert (
        "document.querySelector('#portfolio-delete')?.addEventListener('click', removePortfolio);"
        in bundle
    )
    assert "button.disabled = !portfolio;" in bundle


def test_served_app_script_exposes_conflict_detail_to_the_page() -> None:
    with TestClient(app) as client:
        page = client.get("/portfolio")
        match = _SHARED_SCRIPT_PATTERN.search(page.text)
        assert match is not None
        shared = client.get(match.group(1))

    assert shared.status_code == 200
    assert "error.detail = detail;" in shared.text
    assert "error.status = response.status;" in shared.text


def test_delete_messages_exist_in_both_locales_with_matching_placeholders() -> None:
    with TestClient(app) as client:
        bundle = _served_bundle(client, "/portfolio")

    used = {key for key in _I18N_KEY_PATTERN.findall(bundle) if key.startswith("portfolio.delete")}
    expected = set(_DELETE_KEY_CONTRACT) - _TEMPLATE_ONLY_KEYS
    assert used == expected, sorted(expected ^ used)

    for key, values in _DELETE_KEY_CONTRACT.items():
        for locale in ("ja", "en"):
            rendered = translate(locale, key, **values)
            # Missing fields raise KeyError above; a leaked placeholder means the
            # catalog and the script disagree about the message contract.
            assert "{" not in rendered, (locale, key, rendered)

    ja_force = translate(
        "ja",
        "portfolio.delete_force_confirm",
        **_DELETE_KEY_CONTRACT["portfolio.delete_force_confirm"],
    )
    en_force = translate(
        "en",
        "portfolio.delete_force_confirm",
        **_DELETE_KEY_CONTRACT["portfolio.delete_force_confirm"],
    )
    for rendered in (ja_force, en_force):
        assert "Core" in rendered
        assert "2" in rendered and "3" in rendered
    assert "保有" in ja_force and "履歴" in ja_force
    assert "holdings" in en_force and "history" in en_force
