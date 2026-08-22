from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = PACKAGE_ROOT / "web" / "static"

PAGE_SCRIPT_ASSETS: dict[str, tuple[str, ...]] = {
    "dashboard": ("dashboard_ux.js",),
    "market": ("market.js",),
    "discover": ("discover.js",),
    "portfolio": ("portfolio.js",),
    "instrument": ("instrument.js", "instrument_ux.js"),
    "research": ("research.js",),
    "compare": ("compare.js",),
    "screener": ("screener.js",),
    "charts": ("charts.js", "charts_ux.js"),
    "rates": ("rates.js",),
    "institutional": ("institutional.js",),
    "edinet": ("edinet.js", "edinet_ux.js"),
    "news": ("news.js",),
    "calendar": ("calendar.js", "calendar_ux.js"),
    "macro": ("macro.js",),
    "alerts": ("alerts.js",),
    "ai": ("ai.js",),
    "settings": ("settings.js", "data_source_settings.js"),
}


def static_asset_path(filename: str) -> Path:
    if not filename or Path(filename).name != filename or "\\" in filename:
        raise ValueError("Static asset names must be simple filenames")
    path = STATIC_ROOT / filename
    if not path.is_file():
        raise FileNotFoundError(filename)
    return path


@lru_cache(maxsize=256)
def static_asset_digest(filename: str) -> str:
    path = static_asset_path(filename)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def page_script_key(path: str) -> str | None:
    if path == "/":
        return "dashboard"
    if path.startswith("/instrument/"):
        return "instrument"
    if path.startswith("/research/"):
        return "research"
    return {
        "/markets": "market",
        "/discover": "discover",
        "/portfolio": "portfolio",
        "/compare": "compare",
        "/screener": "screener",
        "/charts": "charts",
        "/rates": "rates",
        "/institutional": "institutional",
        "/edinet": "edinet",
        "/news": "news",
        "/calendar": "calendar",
        "/macro": "macro",
        "/alerts": "alerts",
        "/ai": "ai",
        "/settings": "settings",
    }.get(path)


@lru_cache(maxsize=len(PAGE_SCRIPT_ASSETS))
def page_script_bundle(key: str) -> tuple[str, bytes]:
    filenames = PAGE_SCRIPT_ASSETS.get(key)
    if filenames is None:
        raise KeyError(key)
    body = b";\n".join(static_asset_path(filename).read_bytes() for filename in filenames)
    digest = hashlib.sha256(body).hexdigest()[:16]
    return digest, body


def static_asset_url(filename: str) -> str:
    if filename.startswith("page:"):
        key = page_script_key(filename.removeprefix("page:"))
        if key is None:
            return ""
        digest, _ = page_script_bundle(key)
        return f"/assets/page/{digest}/{key}.js"
    return f"/assets/static/{static_asset_digest(filename)}/{filename}"
