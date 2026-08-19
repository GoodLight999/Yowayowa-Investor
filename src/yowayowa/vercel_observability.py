from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import traceback
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request

_ARCHIVE_SHA = re.compile(r"/archive/([0-9a-fA-F]{7,40})\.zip(?:$|[?#])")
_LOGGER = logging.getLogger("yowayowa.vercel")

if not _LOGGER.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _LOGGER.addHandler(handler)
_LOGGER.setLevel(logging.INFO)
_LOGGER.propagate = False


def _package_revision() -> str | None:
    try:
        direct_url = distribution("yowayowa-investor").read_text("direct_url.json")
    except PackageNotFoundError:
        return None
    if not direct_url:
        return None
    try:
        payload = json.loads(direct_url)
    except json.JSONDecodeError:
        return None
    vcs_info = payload.get("vcs_info")
    if isinstance(vcs_info, dict):
        commit_id = vcs_info.get("commit_id")
        if isinstance(commit_id, str) and commit_id:
            return commit_id
    source_url = payload.get("url")
    if not isinstance(source_url, str):
        return None
    match = _ARCHIVE_SHA.search(source_url)
    return match.group(1) if match else None


@lru_cache(maxsize=1)
def runtime_source_info() -> dict[str, str | None]:
    try:
        package_version = version("yowayowa-investor")
    except PackageNotFoundError:
        package_version = None
    return {
        "package_version": package_version,
        "source_revision": (
            os.getenv("YOWAYOWA_BUILD_SHA")
            or os.getenv("VERCEL_GIT_COMMIT_SHA")
            or _package_revision()
        ),
    }


def _vercel_runtime() -> dict[str, str | None]:
    return {
        "deployment_id": os.getenv("VERCEL_DEPLOYMENT_ID"),
        "environment": os.getenv("VERCEL_ENV"),
        "region": os.getenv("VERCEL_REGION"),
        "project_id": os.getenv("VERCEL_PROJECT_ID"),
        "production_url": os.getenv("VERCEL_PROJECT_PRODUCTION_URL"),
    }


def _request_id(request: Request) -> str:
    return request.headers.get("x-vercel-id") or request.headers.get("x-request-id") or uuid4().hex


def _safe_traceback(exc: BaseException) -> list[dict[str, str | int]]:
    frames = traceback.extract_tb(exc.__traceback__)[-6:]
    return [
        {
            "file": Path(frame.filename).name,
            "line": int(frame.lineno or 0),
            "function": frame.name,
        }
        for frame in frames
    ]


def _emit(payload: dict[str, object], *, error: bool = False) -> None:
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    (_LOGGER.error if error else _LOGGER.info)(line)


def install_vercel_observability(app: FastAPI) -> None:
    """Install Vercel-oriented request correlation without logging secrets or payloads."""

    if getattr(app.state, "vercel_observability_installed", False):
        return
    app.state.vercel_observability_installed = True

    @app.middleware("http")
    async def vercel_request_observability(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = _request_id(request)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            _emit(
                {
                    "level": "error",
                    "event": "http.exception",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    "error_type": type(exc).__name__,
                    "trace": _safe_traceback(exc),
                    **runtime_source_info(),
                    **_vercel_runtime(),
                },
                error=True,
            )
            raise

        response.headers["x-yowayowa-request-id"] = request_id
        if not request.url.path.startswith("/static/"):
            status_code = response.status_code
            _emit(
                {
                    "level": "error" if status_code >= 500 else "info",
                    "event": "http.request",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    **runtime_source_info(),
                    **_vercel_runtime(),
                },
                error=status_code >= 500,
            )
        return response

    @app.get("/internal/debug/runtime", include_in_schema=False)
    async def vercel_runtime_debug(request: Request) -> dict[str, object]:
        return {
            "status": "ok",
            "request_id": _request_id(request),
            "source": runtime_source_info(),
            "vercel": _vercel_runtime(),
        }
