from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from yowayowa.acquisition.models import AuthState

_BODY_MARKER_SCAN_LIMIT = 200_000


@dataclass(frozen=True)
class AuthSignal:
    url: str | None = None
    status_code: int | None = None
    title: str | None = None
    body_text: str | None = None


class AuthDetector(Protocol):
    def detect(self, signal: AuthSignal) -> AuthState: ...


class HeuristicAuthDetector:
    """Fail-closed heuristic detection of an authenticated-session response.

    UNAUTHENTICED when the transport reports 401/403, the URL path contains a
    login marker, or the title/body carries a login text marker within a small
    body (large bodies only match via URL/status signals, since portals embed
    login words in unrelated chrome). AUTHENTICATED only for 2xx/3xx responses.
    """

    def __init__(
        self,
        *,
        login_url_markers: tuple[str, ...] = ("login", "signin", "sign-in", "auth"),
        login_text_markers: tuple[str, ...] = ("ログイン", "サインイン", "Sign in", "Log in"),
    ) -> None:
        self.login_url_markers = login_url_markers
        self.login_text_markers = login_text_markers

    def detect(self, signal: AuthSignal) -> AuthState:
        if (
            signal.url is None
            and signal.status_code is None
            and signal.title is None
            and signal.body_text is None
        ):
            return AuthState.UNKNOWN
        if signal.status_code in (401, 403):
            return AuthState.UNAUTHENTICED
        if signal.url is not None and self._url_marks_login(signal.url):
            return AuthState.UNAUTHENTICED
        body = signal.body_text or ""
        haystack = f"{signal.title or ''}\n{body}"
        if any(marker in haystack for marker in self.login_text_markers) and (
            len(body) < _BODY_MARKER_SCAN_LIMIT
        ):
            return AuthState.UNAUTHENTICED
        if signal.status_code is not None and 200 <= signal.status_code < 400:
            return AuthState.AUTHENTICATED
        return AuthState.UNKNOWN

    def _url_marks_login(self, url: str) -> bool:
        path = urlsplit(url).path.lower()
        return any(marker in path for marker in self.login_url_markers)
