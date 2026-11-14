# Hyperliquid本番口座の確認と移行時の安全条件

2026-11-03版は `evidence/hl_exec_disposition_20261108/historical_runbooks/` に履歴保存。旧版の入金推奨額、固定アドレス、元本保全の断定を現行の操作指示として使わない。本整理では入出金・署名・実注文を行っていない。

## 移行前に確定すること

- 口座の所有者・鍵の保管責任者・承認を行うウォレット・運用対象アドレスを照合する。
- 接続した個人ウォレットでAPIウォレットを承認しても、別のシステム所有口座への承認にはならない。送金先を画面に貼るだけで所有権は移らない。
- 過去の残高・承認・鍵modeの記録は現在の証明ではない。既存鍵をチャットへ渡したり、移行確認のために新規鍵を生成したりしない。
- 実弾化は独立検収とマスターの明示裁可が前提。入金・承認済みを自動売買の許可と解釈しない。

## 読み取り確認（秘密鍵不要）

対象は `https://api.hyperliquid.xyz/info`。対象口座のアドレスを正確に指定し、以下を取得する。

| type | 確認する事実 |
|---|---|
| `clearinghouseState` | `marginSummary.accountValue`、`assetPositions`。欠損を0に補完しない |
| `extraAgents` | 予定したエージェントのアドレス・名前・期限の一致 |
| `openOrders` | 有効な配列として未約定注文を確認。不正応答を空配列とみなさない |

取得先・対象口座・取得時刻を保存する。1項目でも取得失敗・欠損・不一致なら「未確認」であり、合格としない。旧 `scripts/verify_mainnet_state.py` は欠損を0扱いし失敗でも成功終了するため不採用となった。履歴ソースは `evidence/hl_exec_disposition_20261108/rejected/` にある。

## 資金・権限の注意

USDCには価格乖離・発行体・チェーン等のリスクがある。手数料・必要ガス・反映時間を固定額や保証として扱わない。入金の必要がある場合も、公式画面で最新の対応ネットワーク・資産・入金先・最低額を確認し、マスター本人が送金先と損失許容額を判断する。この文書は送金を指示しない。

APIウォレットに出金権限がなくても、取引で資金を失うことはある。ホストが侵害され、同じホストにマスター鍵もあれば、エージェントの出金制限だけでは資金を守れない。分離証拠金や停止閾値も約定・清算・通信障害下の最大損失を保証しない。

現在の `HyperliquidExchangeClient` はテストネット用で、本カードで本番接続を解禁していない。旧S-07自動実行も停止標識に置き換えた。移行前には実際のSDK・接続先・発注意味論・注文不明時の照合・停止経路を別途検収する。

公式根拠:
- https://hyperliquid.gitbook.io/hyperliquid-docs/onboarding/how-to-start-trading
- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint
