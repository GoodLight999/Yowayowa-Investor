# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""testnet 接続先の厳密 allowlist（正規化後・完全一致のみ）。

部分一致・前方一致・suffix一致を禁止する。判定は次の正規化規則を通した
**完全一致**だけで行う（規則はこの docstring が正本）:

1. 入力は ``str`` で、空でないこと。
2. ASCII 印字可能文字のみ。空白・制御文字・非ASCII（IDN含む）・``\\`` は拒否。
3. スキームは生文字列が ``https://`` で始まること（``HTTPS://`` は拒否）。
4. userinfo（``user:pass@``）は拒否。
5. ホストは既に小文字であること（大文字化は正規化せず拒否）。末尾ドット拒否。
6. ホストは ``api.hyperliquid-testnet.xyz`` と完全一致（サブドメイン・
   suffix・prefix は一致しない）。
7. ポートは省略または ``443`` のみ。``:443`` は省略形へ正規化し、他ポートは拒否。
8. パスは空または ``/`` のみ（``/evil`` 等は拒否）。
9. クエリ・フラグメントは存在自体を拒否（``?``/``#`` を含む文字列は拒否）。

結果は常に ``https://api.hyperliquid-testnet.xyz`` へ正規化される。
"""

from __future__ import annotations

from urllib.parse import urlsplit

CANONICAL_TESTNET_BASE_URL = "https://api.hyperliquid-testnet.xyz"
ALLOWED_SCHEME = "https"
ALLOWED_HOST = "api.hyperliquid-testnet.xyz"
ALLOWED_PORT = 443


class TestnetURLRejected(ValueError):
    """接続先が testnet allowlist の境界契約を満たさない（生成時に停止する）。"""

    __test__ = False  # pytest にテストクラスとして収集させない


def _reject(raw: object, reason: str) -> TestnetURLRejected:
    return TestnetURLRejected(
        "order path must use the canonical Hyperliquid testnet endpoint "
        f"({CANONICAL_TESTNET_BASE_URL}); {reason}; got: {raw!r}"
    )


def canonicalize_testnet_url(raw: object) -> str:
    """接続先を検証し、正規形の testnet base URL を返す。"""
    if not isinstance(raw, str):
        raise _reject(raw, f"expected str, got {type(raw).__name__}")
    if not raw:
        raise _reject(raw, "empty URL")
    for char in raw:
        codepoint = ord(char)
        if codepoint <= 0x20 or codepoint == 0x7F:
            raise _reject(raw, "whitespace or control character is not allowed")
        if codepoint > 0x7E:
            raise _reject(raw, "non-ASCII host (IDN) is not allowed")
    if "\\" in raw:
        raise _reject(raw, "backslash is not allowed")
    if not raw.startswith(f"{ALLOWED_SCHEME}://"):
        raise _reject(raw, f"scheme must be exactly {ALLOWED_SCHEME}:// (lowercase)")
    if "?" in raw or "#" in raw:
        raise _reject(raw, "query and fragment are not allowed")

    try:
        parts = urlsplit(raw)
    except ValueError as exc:  # 不正なIPv6・ポート等
        raise _reject(raw, f"unparseable URL ({exc})") from exc

    if parts.scheme != ALLOWED_SCHEME:
        raise _reject(raw, f"scheme must be exactly {ALLOWED_SCHEME}")
    if parts.username is not None or parts.password is not None:
        raise _reject(raw, "userinfo is not allowed")
    host = parts.hostname
    if host is None:
        raise _reject(raw, "missing host")
    raw_host = parts.netloc.rsplit(":", 1)[0] if ":" in parts.netloc else parts.netloc
    if raw_host != raw_host.lower():
        raise _reject(raw, "host must already be lowercase")
    if host.endswith("."):
        raise _reject(raw, "trailing dot in host is not allowed")
    if host != ALLOWED_HOST:
        raise _reject(raw, f"host must be exactly {ALLOWED_HOST}")
    try:
        port = parts.port
    except ValueError as exc:
        raise _reject(raw, f"invalid port ({exc})") from exc
    if port not in (None, ALLOWED_PORT):
        raise _reject(raw, f"port must be {ALLOWED_PORT} or omitted")
    if parts.path not in ("", "/"):
        raise _reject(raw, "path must be empty or '/'")
    return CANONICAL_TESTNET_BASE_URL


def assert_testnet_exchange(exchange: object) -> str:
    """注入された client/transport 自身の ``base_url`` を同じ規則で照合する。

    ``base_url`` を持たない注入物は「照合不能」であり、fail-closed として拒否する。
    """
    if not hasattr(exchange, "base_url"):
        raise _reject(
            exchange,
            "injected exchange has no base_url, so its destination cannot be verified",
        )
    declared = exchange.base_url
    try:
        return canonicalize_testnet_url(declared)
    except TestnetURLRejected as exc:
        raise _reject(declared, f"injected exchange is not the canonical testnet ({exc})") from exc
