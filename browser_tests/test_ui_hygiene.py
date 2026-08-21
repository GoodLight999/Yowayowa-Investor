from __future__ import annotations

import re

from playwright.sync_api import Page

BASE_URL = "http://127.0.0.1:8000"

_SURFACES = (
    "/",
    "/markets",
    "/discover",
    "/portfolio",
    "/settings",
    "/ai",
    "/compare",
    "/screener",
    "/charts",
    "/calendar",
    "/news",
    "/macro",
    "/rates",
    "/institutional",
    "/edinet",
    "/alerts",
    "/instrument/AAPL",
)

_RAW_TRANSLATION_KEY_RE = re.compile(
    r"\b(?:common|nav|dashboard|market|discover|portfolio|settings|ai|alerts|events|calendar|"
    r"instrument|compare|screener|chart|charts|news|macro|rate|rates|institutional|edinet|"
    r"licensing|risk|sector)\.[A-Za-z0-9_.-]+\b"
)


def _settle(page: Page, path: str) -> None:
    response = page.goto(f"{BASE_URL}{path}", wait_until="domcontentloaded")
    assert response is not None and response.ok, path
    page.wait_for_timeout(120)


def test_primary_surfaces_do_not_create_root_horizontal_overflow_on_phone(page: Page) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    for path in _SURFACES:
        _settle(page, path)
        metrics = page.evaluate(
            """() => ({
                scrollWidth: document.documentElement.scrollWidth,
                clientWidth: document.documentElement.clientWidth,
            })"""
        )
        assert metrics["scrollWidth"] <= metrics["clientWidth"] + 1, (
            path,
            metrics,
        )
        visible_text = page.locator("body").inner_text()
        leaked_key = _RAW_TRANSLATION_KEY_RE.search(visible_text)
        assert leaked_key is None, (path, leaked_key.group(0) if leaked_key else None)


def test_visible_interactive_controls_keep_readable_type_and_target_size(page: Page) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    control_selector = (
        "button, input:not([type='hidden']), select, textarea, .nav a, details > summary"
    )
    supporting_text_selector = "main small, main label, main p, main .muted"
    for path in _SURFACES:
        _settle(page, path)
        violations = page.locator(control_selector).evaluate_all(
            """elements => elements.flatMap(el => {
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                const visible = style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && Number(style.opacity) !== 0
                    && rect.width > 0
                    && rect.height > 0;
                if (!visible || el.disabled) return [];
                const type = (el.getAttribute('type') || '').toLowerCase();
                const compact = type === 'checkbox' || type === 'radio';
                const fontSize = Number.parseFloat(style.fontSize);
                const problems = [];
                if (!compact && fontSize < 12) problems.push(`font ${fontSize}px`);
                if (!compact && rect.height < 36) {
                    problems.push(`height ${rect.height.toFixed(1)}px`);
                }
                if (problems.length === 0) return [];
                const label = el.textContent || el.getAttribute('aria-label') || '';
                return [{
                    tag: el.tagName.toLowerCase(),
                    id: el.id || null,
                    className: typeof el.className === 'string' ? el.className : '',
                    text: label.trim().slice(0, 80),
                    problems,
                }];
            })"""
        )
        assert violations == [], (path, violations)

        readability_violations = page.locator(supporting_text_selector).evaluate_all(
            """elements => elements.flatMap(el => {
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                const visible = style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && Number(style.opacity) !== 0
                    && rect.width > 0
                    && rect.height > 0;
                if (!visible) return [];
                const fontSize = Number.parseFloat(style.fontSize);
                if (fontSize >= 11) return [];
                const text = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 80);
                if (!text) return [];
                return [{
                    tag: el.tagName.toLowerCase(),
                    id: el.id || null,
                    className: typeof el.className === 'string' ? el.className : '',
                    text,
                    fontSize,
                }];
            })"""
        )
        assert readability_violations == [], (path, readability_violations)
