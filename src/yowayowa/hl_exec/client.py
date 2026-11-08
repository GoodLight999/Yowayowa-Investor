# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""Hyperliquid 公開API（/info）読み取り専用クライアント。

設計上の約束（docs/CIO_HL_EXECUTION_TRACK_PLAN.md §1・§2.1・§2.2-3）:
  * 認証なし。秘密鍵・APIウォレット・電子署名は一切扱わない。
  * 使うのは ``POST /info`` のみ（公開情報エンドポイント）。
  * ``/exchange``（発注・取消などの送信系）は呼ばない ＝ 実注文 0 件。
  * 送信系は本段階では未実装であり、呼び出した時点で明示的に例外を出す
    （うっかり送信経路を後から足せないようにする物理遮断の契約）。

先行実装 ``scripts/s07_paper/hl_client.py``（ゼロ依存・urllib）の規約
（User-Agent・4xx即死・リトライ付きバックオフ）を踏襲する。
テストは ``transport`` 注入でモックし、実ネットワークには触れない。

出典（2026-11-02 再確認）:
  * Info Endpoint: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
  * Perpetuals:    https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

INFO_URL_MAINNET = "https://api.hyperliquid.xyz/info"
INFO_URL_TESTNET = "https://api.hyperliquid-testnet.xyz/info"
TESTNET_API_URL = "https://api.hyperliquid-testnet.xyz"
USER_AGENT = "yowayowa-hl-exec/0.1 (read-only)"

_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

# transport: (payload, url, timeout) -> 応答JSONオブジェクト（テスト注入点）
Transport = Callable[[dict[str, Any], str, float], Any]
# sleeper: 秒を受け取り待機する（テストでは即時返しに差し替える）
Sleeper = Callable[[float], None]


class HLError(RuntimeError):
    """公開APIが想定外の応答を返した、または到達できなかったとき。"""


class ExchangeWriteNotImplemented(RuntimeError):
    """送信系（/exchange・取消・鍵・電子署名）は本段階では未実装という恒久的契約。

    この例外は「まだ実装していない」という一時的状態ではなく、
    第1段の設計契約そのもの（送信コード 0 をこのパッケージで保証する）。
    送信を必要とする後段カードは、公式SDKを別経路で使う。
    """


def _validate_address(user: str) -> str:
    """/info の user は42文字の0xで始まるオンチェーンアドレス（公式仕様）。"""
    if not isinstance(user, str) or not _EVM_ADDRESS_RE.match(user):
        raise ValueError(f"invalid address (expected 0x + 40 hex chars): {user!r}")
    return user


def _default_transport(payload: dict[str, Any], url: str, timeout: float) -> Any:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _noop_sleeper(_seconds: float) -> None:  # pragma: no cover - 無操作
    """待機しない（既定ではない。テスト注入用の実体）。"""


class HyperliquidInfoClient:
    """読み取り専用クライアント。実ネットワークへは /info の POST だけを行う。"""

    def __init__(
        self,
        base_url: str = INFO_URL_MAINNET,
        *,
        transport: Transport | None = None,
        sleeper: Sleeper | None = None,
        timeout: float = 30.0,
        retries: int = 3,
    ) -> None:
        if retries < 1:
            raise ValueError("retries must be >= 1")
        self._base_url = base_url
        self._transport: Transport = transport if transport is not None else _default_transport
        self._sleep: Sleeper = sleeper if sleeper is not None else time.sleep
        self._timeout = float(timeout)
        self._retries = int(retries)

    # ------------------------------------------------------------------ 内部
    def _post(self, payload: dict[str, Any]) -> Any:
        last: Exception | None = None
        for attempt in range(self._retries):
            try:
                return self._transport(payload, self._base_url, self._timeout)
            except urllib.error.HTTPError as exc:
                detail = exc.read()[:200].decode("utf-8", "replace")
                last = HLError(f"HTTP {exc.code}: {detail}")
                if 400 <= exc.code < 500:
                    raise last from exc  # 4xxは即死（先行 s07 クライアントと同一規約）
            except (
                urllib.error.URLError,
                TimeoutError,
                ConnectionError,
                json.JSONDecodeError,
            ) as exc:
                last = exc
            self._sleep(min(1.5 * (attempt + 1), 5.0))
        raise HLError(f"Hyperliquid info request failed after {self._retries} tries: {last}")

    def _require_dict(self, raw: Any, *, what: str) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise HLError(f"unexpected {what} payload: {type(raw).__name__}")
        return raw

    def _require_list(self, raw: Any, *, what: str) -> list[Any]:
        if not isinstance(raw, list):
            raise HLError(f"unexpected {what} payload: {type(raw).__name__}")
        return raw

    # ---------------------------------------------------------------- 読み取り
    def clearinghouse_state(self, user: str) -> dict[str, Any]:
        """口座サマリ（残高・分離証拠金・建玉）。アドレス文字列だけで読める（鍵不要）。"""
        _validate_address(user)
        return self._require_dict(
            self._post({"type": "clearinghouseState", "user": user}),
            what="clearinghouseState",
        )

    def open_orders(self, user: str) -> list[Any]:
        """未約定注文一覧（キルスイッチ照合・Step 4 の「注文0件」確認に使う）。"""
        _validate_address(user)
        raw = self._post({"type": "openOrders", "user": user})
        return self._require_list(raw, what="openOrders")

    def user_fills(self, user: str) -> list[Any]:
        """約定履歴一覧（照合用・新規約定0件の証明に使う）。"""
        _validate_address(user)
        raw = self._post({"type": "userFills", "user": user})
        return self._require_list(raw, what="userFills")

    def l2_book(self, coin: str = "BTC") -> dict[str, Any]:
        """板情報（L2）。生応答をそのまま返す。"""
        if not isinstance(coin, str) or not coin:
            raise ValueError("coin must be a non-empty string")
        return self._require_dict(self._post({"type": "l2Book", "coin": coin}), what="l2Book")

    def meta(self) -> dict[str, Any]:
        """ユニバース全体のメタ（各銘柄の szDecimals・maxLeverage 等）。"""
        return self._require_dict(self._post({"type": "meta"}), what="meta")

    def asset_meta(self, coin: str = "BTC") -> dict[str, Any]:
        """銘柄1つ分のメタ。wire の ``a`` フィールドに必要な asset index も返す。

        出典: 公式 Exchange Endpoint「For perpetuals this is the index in the
        universe field returned by the meta response」。
        """
        raw = self.meta()
        universe = raw.get("universe")
        if not isinstance(universe, list):
            raise HLError("meta response has no universe list")
        for index, asset in enumerate(universe):
            if isinstance(asset, dict) and asset.get("name") == coin:
                return {
                    "coin": coin,
                    "asset_index": int(index),
                    "szDecimals": int(asset["szDecimals"]),
                    "maxLeverage": int(asset["maxLeverage"]),
                    "onlyIsolated": bool(asset.get("onlyIsolated", False)),
                }
        raise HLError(f"coin {coin} not found in meta universe")

    def top_of_book(self, coin: str = "BTC") -> dict[str, float]:
        """最良気配とスプレッド（先行 s07 クライアントと同一の正規化形）。"""
        raw = self.l2_book(coin)
        levels = raw.get("levels") or [[], []]
        bids, asks = levels[0], levels[1]
        if not bids or not asks:
            raise HLError("empty L2 book")
        bid = float(bids[0]["px"])
        ask = float(asks[0]["px"])
        mid = (bid + ask) / 2.0
        return {
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "spread_bps": (ask - bid) / mid * 10_000.0,
            "half_spread_bps": (ask - bid) / 2.0 / mid * 10_000.0,
        }

    # --------------------------------------------------- 送信系（恒久的に未実装）
    def submit_order(self, *_args: Any, **_kwargs: Any) -> Any:
        """発注・取消・口座操作など /exchange への送信は本段階では実装しない。

        第1段は「完全ドライラン」であり、このパッケージに送信経路を置かない。
        呼び出した時点で例外（決して黙って成功しない）。
        """
        raise ExchangeWriteNotImplemented(
            "order submission is intentionally not implemented in phase 1; "
            "this package stays read-only (docs/CIO_HL_EXECUTION_TRACK_PLAN.md)"
        )

    def cancel_orders(self, *_args: Any, **_kwargs: Any) -> Any:
        """未約定取消も送信系であるため本段階では実装しない（submit_order と同じ契約）。"""
        raise ExchangeWriteNotImplemented(
            "order cancellation is intentionally not implemented in phase 1; "
            "kill switch runs with an injected cancel callback instead"
        )


class HyperliquidExchangeClient:
    """Hyperliquid 取引所送信クライアント（第4段: テストネット注文往復用）。

    制約契約:
      * メインネット（api.hyperliquid.xyz）への接続は厳格に禁止（即時例外）。
      * 秘密鍵はメモリ内のみで保持し、ログ・文字列表現（__repr__）には絶対に出力しない。
      * 発注前に killswitch が設定されている場合は ensure_may_trade() で遮断する。
    """

    def __init__(
        self,
        account_address: str,
        agent_key: str | Path,
        *,
        base_url: str = TESTNET_API_URL,
        killswitch: Any | None = None,
        exchange: Any | None = None,
    ) -> None:
        _validate_address(account_address)
        if "api.hyperliquid.xyz" in base_url and "testnet" not in base_url:
            raise ValueError(f"Mainnet endpoint is strictly forbidden: {base_url}")
        self._account_address = account_address
        self._base_url = base_url
        self._killswitch = killswitch

        if exchange is not None:
            self._exchange = exchange
        else:
            try:
                if isinstance(agent_key, Path) or (
                    isinstance(agent_key, str) and (os.path.exists(agent_key) or "/" in agent_key)
                ):
                    key_str = Path(agent_key).read_text(encoding="utf-8").strip()
                else:
                    key_str = agent_key
                from eth_account import Account
                from hyperliquid.exchange import Exchange

                account = Account.from_key(key_str)
                self._exchange = Exchange(
                    account, base_url=self._base_url, account_address=self._account_address
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to initialize Hyperliquid exchange client: {type(exc).__name__}"
                ) from None

    @property
    def account_address(self) -> str:
        return self._account_address

    @property
    def base_url(self) -> str:
        return self._base_url

    def __repr__(self) -> str:
        return (
            f"HyperliquidExchangeClient(account_address={self._account_address!r}, "
            f"base_url={self._base_url!r})"
        )

    def order(
        self,
        coin: str,
        is_buy: bool,
        sz: float,
        px: float,
        order_type: dict[str, Any],
        *,
        cloid: str | None = None,
        reduce_only: bool = False,
    ) -> dict[str, Any]:
        """注文を発注する。発注前にキルスイッチの取引可否を検証する。"""
        if self._killswitch is not None:
            self._killswitch.ensure_may_trade()
        # Order submission is not retry-safe: a TypeError can occur after acceptance.
        # Keep reduce_only intact; SDK-incompatible test doubles must be fixed in tests.
        res: Any = self._exchange.order(
            coin, is_buy, sz, px, order_type, reduce_only=reduce_only, cloid=cloid
        )
        if not isinstance(res, dict):
            raise HLError(f"unexpected exchange order response: {type(res).__name__}")
        return res

    def cancel(self, coin: str, oid: int) -> dict[str, Any]:
        """oid で未約定注文を取り消す。"""
        res: Any = self._exchange.cancel(coin, oid)
        if not isinstance(res, dict):
            raise HLError(f"unexpected exchange cancel response: {type(res).__name__}")
        return res

    def cancel_by_cloid(self, coin: str, cloid: str) -> dict[str, Any]:
        """cloid で未約定注文を取り消す。"""
        res: Any = self._exchange.cancel_by_cloid(coin, cloid)
        if not isinstance(res, dict):
            raise HLError(f"unexpected exchange cancel_by_cloid response: {type(res).__name__}")
        return res
