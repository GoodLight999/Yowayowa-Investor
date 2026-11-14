from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import traceback
from contextvars import ContextVar
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from starlette.responses import PlainTextResponse

from yowayowa.api.deps import operator_token_held
from yowayowa.db import positions_unique_constraint_status

_ARCHIVE_SHA = re.compile(r"/archive/([0-9a-fA-F]{7,40})\.zip(?:$|[?#])")
_LOGGER = logging.getLogger("yowayowa.vercel")
_ASSET_PREFIXES = ("/static/", "/assets/")
_IMMUTABLE_ASSET_CDN_CACHE = "public, max-age=31536000, immutable"

if not _LOGGER.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _LOGGER.addHandler(handler)
_LOGGER.setLevel(logging.INFO)
_LOGGER.propagate = False

#: Fixed client-facing wording for upstream (502) failures. Route handlers map
#: provider defects to this message; the exception type, message, and URL are
#: preserved only in the structured ``http.exception`` log line.
UPSTREAM_UNAVAILABLE_DETAIL = "Upstream data source is temporarily unavailable"

#: Request correlation captured by the middleware. Handled upstream failures
#: (502) never reach the middleware's except branch, so their log lines pull
#: request_id / method / path from here to stay correlatable.
_REQUEST_CONTEXT: ContextVar[dict[str, str] | None] = ContextVar(
    "yowayowa_request_context", default=None
)


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
    existing = getattr(request.state, "yowayowa_request_id", None)
    if isinstance(existing, str) and existing:
        return existing
    return (
        request.headers.get("x-yowayowa-request-id")
        or request.headers.get("x-vercel-id")
        or request.headers.get("x-request-id")
        or uuid4().hex
    )


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


def log_upstream_exception(exc: BaseException) -> None:
    """Emit an ``http.exception`` line for a handled upstream failure.

    Routes translate provider defects into HTTP 502 whose body carries only a
    fixed message; the exception type, message, and traceback survive here
    (and only here), correlated with the request via ``request_id``.
    """
    context = _REQUEST_CONTEXT.get() or {}
    _emit(
        {
            "level": "error",
            "event": "http.exception",
            "request_id": context.get("request_id"),
            "method": context.get("method"),
            "path": context.get("path"),
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "trace": _safe_traceback(exc),
            **runtime_source_info(),
            **_vercel_runtime(),
        },
        error=True,
    )


def install_vercel_observability(app: FastAPI) -> None:
    """Install Vercel request correlation and production-specific asset caching."""

    if getattr(app.state, "vercel_observability_installed", False):
        return
    app.state.vercel_observability_installed = True

    @app.middleware("http")
    async def vercel_request_observability(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = _request_id(request)
        request.state.yowayowa_request_id = request_id
        context_token = _REQUEST_CONTEXT.set(
            {
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
            }
        )
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
        finally:
            _REQUEST_CONTEXT.reset(context_token)

        response.headers["x-yowayowa-request-id"] = request_id
        is_asset_request = request.url.path.startswith(_ASSET_PREFIXES)
        if request.url.path.startswith("/assets/") and response.status_code < 400:
            response.headers["Vercel-CDN-Cache-Control"] = _IMMUTABLE_ASSET_CDN_CACHE
        if not is_asset_request:
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

    @app.exception_handler(Exception)
    async def vercel_unhandled_exception(request: Request, exc: Exception) -> PlainTextResponse:
        """Keep request correlation on the generic 500 built by ServerErrorMiddleware.

        The http middleware above logs the exception and re-raises, so the 500
        response is assembled outside it; the header must be set here or a
        failing request cannot be matched with its structured log line.
        """

        response = PlainTextResponse("Internal Server Error", status_code=500)
        response.headers["x-yowayowa-request-id"] = _request_id(request)
        return response

    @app.get("/internal/debug/runtime", include_in_schema=False)
    async def vercel_runtime_debug(request: Request) -> dict[str, object]:
        """Liveness for every caller; build/runtime detail only with the token.

        Anonymous callers (token unset, mismatched, or header absent) get only
        the correlation fields; ``source``/``vercel`` build identification and
        the database migration status are returned solely when the exact
        operator Bearer token is presented.
        """
        payload: dict[str, object] = {
            "status": "ok",
            "request_id": _request_id(request),
        }
        if operator_token_held(request.headers.get("authorization")):
            payload["source"] = runtime_source_info()
            payload["vercel"] = _vercel_runtime()
            payload["database"] = {
                "positions_unique_constraint": positions_unique_constraint_status(),
            }
        return payload
