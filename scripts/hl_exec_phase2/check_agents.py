#!/usr/bin/env python3
# ruff: noqa: RUF001, RUF002, RUF003
"""既存エージェント承認の有無を /info だけで照会する（読み取り専用・鍵不要）。

approveAgent 手順書の「完了確認」に使うコマンドの実在確認用。
  * type=extraAgents: 承認済みエージェント一覧
  * type=userRole:    アドレスのロール（user / agent / vault など）
/exchange へは一切送らない。
"""

from __future__ import annotations

import json
import sys
import urllib.request

INFO_URL_TESTNET = "https://api.hyperliquid-testnet.xyz/info"
ACCOUNT = "0xF6cB1079F73cA0163E2EfF24eFbB651f99E2Dd82"


def post(payload: dict) -> tuple[int, object]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        INFO_URL_TESTNET,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "yowayowa-hl-exec/0.1 (read-only)"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - 固定 https
        return resp.getcode(), json.loads(resp.read().decode())


def main() -> int:
    for payload in (
        {"type": "extraAgents", "user": ACCOUNT},
        {"type": "userRole", "user": ACCOUNT},
    ):
        status, body = post(payload)
        print(f"POST /info {payload['type']}: HTTP {status} -> {json.dumps(body, ensure_ascii=False)[:400]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
