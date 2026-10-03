from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import APIResponse, BrowserContext, Page, Playwright, sync_playwright


class BrokerWebSessionError(RuntimeError):
    pass


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("broker web origin must be an absolute HTTP(S) URL")
    return parsed.scheme.lower(), parsed.hostname.lower(), parsed.port


class PersistentBrokerWebSession:
    """Local persistent browser session for an operator's own broker account.

    Authentication is deliberately interactive. The operator completes the broker's
    normal login/MFA/device flow in the visible browser. Session material stays in
    the local Chromium user-data directory.

    BrowserContext.request shares the same cookie jar as the browser context, so a
    broker-specific connector may combine DOM automation with same-session HTTP calls.
    """

    def __init__(
        self,
        *,
        base_url: str,
        profile_dir: str | Path,
        headless: bool = False,
        channel: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self._base_origin = _origin(self.base_url)
        self.profile_dir = Path(profile_dir).expanduser()
        self.headless = headless
        self.channel = channel
        self.user_agent = user_agent
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None

    @property
    def started(self) -> bool:
        return self._context is not None

    def _relative_url(self, path: str) -> str:
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc:
            raise BrokerWebSessionError("broker web-session paths must be relative")
        url = urljoin(self.base_url, path.lstrip("/"))
        if _origin(url) != self._base_origin:
            raise BrokerWebSessionError("broker web-session request escaped configured origin")
        return url

    def start(self) -> None:
        if self._context is not None:
            return
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        with suppress(OSError):
            os.chmod(self.profile_dir, 0o700)

        playwright = sync_playwright().start()
        try:
            kwargs: dict[str, Any] = {
                "headless": self.headless,
                "accept_downloads": False,
            }
            if self.channel is not None:
                kwargs["channel"] = self.channel
            if self.user_agent:
                kwargs["user_agent"] = self.user_agent
            context = playwright.chromium.launch_persistent_context(
                str(self.profile_dir),
                **kwargs,
            )
        except Exception:
            playwright.stop()
            raise

        self._playwright = playwright
        self._context = context

    def close(self) -> None:
        context = self._context
        playwright = self._playwright
        self._context = None
        self._playwright = None
        if context is not None:
            context.close()
        if playwright is not None:
            playwright.stop()

    def page(self) -> Page:
        if self._context is None:
            raise BrokerWebSessionError("broker web session has not been started")
        pages = self._context.pages
        if pages:
            return pages[0]
        return self._context.new_page()

    def open(self, path: str = "") -> Page:
        page = self.page()
        page.goto(self._relative_url(path), wait_until="domcontentloaded")
        return page

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str | float | bool] | None = None,
        data: object | None = None,
        form: dict[str, str | float | bool] | None = None,
        timeout_ms: float = 30_000,
    ) -> APIResponse:
        if self._context is None:
            raise BrokerWebSessionError("broker web session has not been started")
        return self._context.request.fetch(
            self._relative_url(path),
            method=method.upper(),
            headers=headers,
            params=params,
            data=data,
            form=form,
            timeout=timeout_ms,
            max_redirects=0,
        )

    def __enter__(self) -> PersistentBrokerWebSession:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
