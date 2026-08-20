from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from yowayowa.web.assets import (
    page_script_bundle,
    static_asset_digest,
    static_asset_path,
)

router = APIRouter(include_in_schema=False)
_IMMUTABLE_HEADERS = {
    "Cache-Control": "public, max-age=31536000, immutable",
    "X-Content-Type-Options": "nosniff",
}


@router.get("/assets/static/{digest}/{filename}")
def fingerprinted_static_asset(digest: str, filename: str) -> FileResponse:
    try:
        path = static_asset_path(filename)
        expected_digest = static_asset_digest(filename)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Unknown static asset") from exc
    if digest != expected_digest:
        raise HTTPException(status_code=404, detail="Unknown static asset")
    return FileResponse(path, headers=_IMMUTABLE_HEADERS)


@router.get("/assets/page/{digest}/{key}.js")
def fingerprinted_page_bundle(digest: str, key: str) -> Response:
    try:
        expected_digest, body = page_script_bundle(key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown page bundle") from exc
    if digest != expected_digest:
        raise HTTPException(status_code=404, detail="Unknown page bundle")
    return Response(
        content=body,
        media_type="text/javascript",
        headers=_IMMUTABLE_HEADERS,
    )
