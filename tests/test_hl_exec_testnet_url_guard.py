# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""testnet URL allowlist の negative/positive 契約（SDK非依存・純関数）。"""

from __future__ import annotations

from typing import Any

import pytest

from yowayowa.hl_exec.client import TESTNET_API_URL
from yowayowa.hl_exec.guards import (
    CANONICAL_TESTNET_BASE_URL,
    TestnetURLRejected,
    assert_testnet_exchange,
    canonicalize_testnet_url,
)

CANONICAL = CANONICAL_TESTNET_BASE_URL


@pytest.mark.parametrize(
    "raw",
    [
        CANONICAL,
        CANONICAL + "/",
        CANONICAL + ":443",
        CANONICAL + ":443/",
    ],
)
def test_accepts_only_canonical_spellings(raw: str) -> None:
    assert canonicalize_testnet_url(raw) == CANONICAL
    assert TESTNET_API_URL == CANONICAL


@pytest.mark.parametrize(
    ("label", "raw"),
    [
        ("mainnet", "https://api.hyperliquid.xyz"),
        ("http-scheme", "http://api.hyperliquid-testnet.xyz"),
        ("uppercase-scheme", "HTTPS://api.hyperliquid-testnet.xyz"),
        ("suffix-attack", "https://api.hyperliquid-testnet.xyz.attacker.invalid"),
        ("prefix-attack", "https://attacker.invalid/api.hyperliquid-testnet.xyz"),
        ("subdomain-attack", "https://evil.api.hyperliquid-testnet.xyz"),
        ("partial-host", "https://api.hyperliquid-testnet.xy"),
        ("lookalike-host", "https://api.hyperliquid-testnet.xyz0"),
        ("other-port", "https://api.hyperliquid-testnet.xyz:8443"),
        ("port-80", "https://api.hyperliquid-testnet.xyz:80"),
        ("userinfo", "https://user:pass@api.hyperliquid-testnet.xyz"),
        ("userinfo-empty-pass", "https://user@api.hyperliquid-testnet.xyz"),
        ("path", "https://api.hyperliquid-testnet.xyz/evil"),
        ("path-dot", "https://api.hyperliquid-testnet.xyz/."),
        ("query", "https://api.hyperliquid-testnet.xyz?redirect=evil"),
        ("fragment", "https://api.hyperliquid-testnet.xyz#frag"),
        ("trailing-dot", "https://api.hyperliquid-testnet.xyz."),
        ("uppercase-host", "https://API.HYPERLIQUID-TESTNET.XYZ"),
        ("idn-cyrillic", "https://\u0430pi.hyperliquid-testnet.xyz"),
        ("idn-punycode", "https://xn--pi-8cd.hyperliquid-testnet.xyz"),
        ("leading-space", " https://api.hyperliquid-testnet.xyz"),
        ("trailing-space", "https://api.hyperliquid-testnet.xyz "),
        ("embedded-newline", "https://api.hyperliquid-testnet.xyz\n.evil"),
        ("backslash-userinfo", "https://api.hyperliquid-testnet.xyz\\@evil.invalid"),
        ("empty", ""),
        ("scheme-only", "https://"),
        ("relative", "api.hyperliquid-testnet.xyz"),
    ],
)
def test_rejects_noncanonical_urls(label: str, raw: str) -> None:
    with pytest.raises(TestnetURLRejected):
        canonicalize_testnet_url(raw)


@pytest.mark.parametrize("raw", [None, 123, b"https://api.hyperliquid-testnet.xyz", True])
def test_rejects_non_string_urls(raw: Any) -> None:
    with pytest.raises(TestnetURLRejected):
        canonicalize_testnet_url(raw)


class _Client:
    def __init__(self, base_url: Any) -> None:
        if base_url is not None:
            self.base_url = base_url


@pytest.mark.parametrize(
    "injected_url",
    [
        "https://api.hyperliquid.xyz",
        "https://api.hyperliquid-testnet.xyz.attacker.invalid",
        "https://api.hyperliquid-testnet.xyz/evil",
        "https://API.HYPERLIQUID-TESTNET.XYZ",
        "https://api.hyperliquid-testnet.xyz:8443",
        "https://user:pass@api.hyperliquid-testnet.xyz",
    ],
)
def test_injected_client_base_url_uses_same_allowlist(injected_url: str) -> None:
    with pytest.raises(TestnetURLRejected, match="injected exchange"):
        assert_testnet_exchange(_Client(injected_url))


def test_injected_client_without_base_url_is_unverifiable_and_rejected() -> None:
    with pytest.raises(TestnetURLRejected, match="no base_url"):
        assert_testnet_exchange(_Client(None))


def test_injected_client_with_canonical_base_url_is_accepted() -> None:
    assert assert_testnet_exchange(_Client(CANONICAL + "/")) == CANONICAL
