from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from yowayowa.web.assets import static_asset_digest, static_asset_path

router = APIRouter(include_in_schema=False)


@router.get("/assets/static/{digest}/{filename}")
def fingerprinted_static_asset(digest: str, filename: str) -> FileResponse:
    try:
        path = static_asset_path(filename)
        expected_digest = static_asset_digest(filename)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Unknown static asset") from exc
    if digest != expected_digest:
        raise HTTPException(status_code=404, detail="Unknown static asset")
    return FileResponse(
        path,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
