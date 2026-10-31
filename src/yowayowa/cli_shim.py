from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx


def _load_app() -> Callable[..., Any]:
    from yowayowa.cli_entry import app

    return app


def _http_failure_line(exc: httpx.HTTPError) -> str:
    """Map a transport-level CLI failure to one fixed operator-safe sentence.

    The boundary must never echo exception text, URLs, tokens, headers,
    response bodies or markup (dead-end audit 2026-10-23 R1 display symptom),
    so every branch returns a constant sentence; only the numeric HTTP status
    is carried through.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            reason = "authentication or permission required"
        elif status == 404:
            reason = "no such target"
        elif status == 422:
            reason = "the API rejected the input"
        elif status == 429:
            reason = "API rate limit reached"
        elif status >= 500:
            reason = "API-side failure"
        else:
            reason = "request failed"
        return f"Yowayowa API request failed: HTTP {status} ({reason})"
    if isinstance(exc, httpx.TimeoutException):
        return "Yowayowa API request failed: the API did not respond in time (timeout)"
    if isinstance(exc, httpx.ConnectError):
        return "Yowayowa API request failed: could not connect to the API"
    return "Yowayowa API request failed: request could not be completed"


def main() -> None:
    try:
        app = _load_app()
    except ModuleNotFoundError as exc:
        if exc.name in {"typer", "rich"}:
            raise SystemExit(
                "Yowayowa CLI requires optional CLI dependencies. "
                "Install with: pip install 'yowayowa-investor[cli]'"
            ) from exc
        raise
    try:
        app()
    except httpx.HTTPError as exc:
        # Only failures that escape the app reach this boundary: commands that
        # handle their own API errors (and click/typer exits, KeyboardInterrupt,
        # non-HTTP bugs) are unaffected. No retry or re-send happens here.
        raise SystemExit(_http_failure_line(exc)) from exc


__all__ = ["main"]


if __name__ == "__main__":
    main()
