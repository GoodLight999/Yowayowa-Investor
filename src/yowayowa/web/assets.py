from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = PACKAGE_ROOT / "web" / "static"


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


def static_asset_url(filename: str) -> str:
    return f"/assets/static/{static_asset_digest(filename)}/{filename}"
