# Hyperliquid 第3段: エージェント鍵の生成とアドレス1本の受渡し（2026-11-02）

カード: `t_22130424` / 実行者: worker-go / 環境: VPS（読み取り専用の照会のみ・重い処理なし）

## 結論

- **テストネット用エージェント鍵をシステム側で生成した。マスターへ渡すのはアドレス1つだけ。**
  渡すアドレス = **`0xc6f0c5c7aca59cd8ba4d56ed798102bc00cf66e5`**（公開情報・`0x`+40桁）
- 秘密鍵は **`/root/.secrets/hl_agent_testnet.key`（mode 600・65 バイト＝64 hex＋LF）**。
  既存の `/root/hyperliquid-testnet-wallet.json` とは**別ファイル**（同ファイルには触れていない）。
- ネットワークは **testnet 固定**（`api.hyperliquid-testnet.xyz`）。メインネット用の鍵は作っていない
  （生成スクリプトは `--network mainnet` を拒否する・単体テストで固定）。
- **署名・発注・approveAgent の実行は 0 件**（`/exchange` へは一切送っていない。`/info` の読み取りのみ）。

## 実測値

| 項目 | 実測 |
| --- | --- |
| `stat /root/.secrets/hl_agent_testnet.key` | `mode=600 size=65 owner=root:root` |
| 親ディレクトリ | `drwx------ /root/.secrets`（700） |
| アドレス控え | `/root/.secrets/hl_agent_testnet.address`（mode 644・公開情報・43 バイト） |
| 再導出の一致 | 保存鍵から導出したアドレス = 控え = 上記アドレス（`verify_agent_key.py` PASS） |
| `clearinghouseState`(エージェント) | **HTTP 200** `accountValue 0.0`・建玉0（未入金・未承認でも読み取りは通る） |
| `userRole`(エージェント) | **HTTP 200** `{"role":"missing"}`（未入金の新規アドレス） |
| `extraAgents`(エージェント) | **HTTP 200** `[]` |
| `extraAgents`(マスター `0xF6cB…Dd82`) | **HTTP 200** `[]`（＝このカードでは承認していない） |
| 単体テスト | `pytest tests/test_hl_exec_phase3_keygen.py tests/test_hl_exec_client.py` → **23 passed** |

500 エラーは 0 件。`/info` の 3 種すべて HTTP 200。

## 生成経路の正しさ（自己検証）

鍵を作る前に、実装を既知ベクタで検証している（壊れた実装で鍵を作らないため）。

- keccak-256: `""` → `c5d2…a470`、`"abc"` → `4e03…6c45`（**純 Python 実装と pycryptodome の両方**で一致）
- 導出: 秘密鍵 `1` → `0x7e5f4552091a69125d5dfcb7b8c2659029395bdf`（既知のベクタと一致）
- 生成後、書き込んだファイルを読み戻して**同じアドレスが再導出できること**を確認（`keygen_output.txt`）

## 秘密鍵の非出力の証明（実測）

`/root/.secrets/hl_agent_testnet.key` の中身を検索パターンにして、リポジトリ・git 履歴・ログ・カード DB を走査した。
**一致 0 件**。走査は「一致した行を出さない」形（`-l` / `-c`）で行い、鍵が画面に出ないようにしている。
実出力は `grep_secret_scan.txt`。

| 走査対象 | 一致 |
| --- | --- |
| worktree（`.git`/`.venv`/`.worktrees` 等を除外） | 0 ファイル |
| `git grep` 全 838 コミット | 0 |
| `.git/objects/pack/*.pack`（生バイト） | 0 |
| `/root/.hermes` の `*.log` / `*.jsonl` / `*.json`（cache 除く） | 0 ファイル |
| `/root/.hermes/kanban.db` | 0 |
| `/root/.hermes/profiles/worker-go/state.db` | 0 |
| `/root/.hermes/kanban/workspaces` | 0 |
| `/root/.hermes/{swarm,docs,scripts}` | 0 |

出力側も構造的に塞いである: 生成・検証スクリプトはアドレス・パス・mode だけを print し、
鍵は `key_shape: 64 hex chars (内容は表示しない)` としか出さない（単体テストで
`stdout/stderr` に鍵文字列が現れないことを assert）。

## 成果物

- `scripts/hl_exec_phase3/gen_agent_key.py` — 生成（testnet 固定・上書き拒否・`os.open` で 0600 作成・自己検証つき）
- `scripts/hl_exec_phase3/verify_agent_key.py` — 検証（mode/再導出/形式/`/info` 3種・鍵非表示）
- `tests/test_hl_exec_phase3_keygen.py` — 8 テスト（既知ベクタ・0600・非出力・上書き拒否・mainnet 拒否）
- `docs/HL_EXEC_APPROVEAGENT_RUNBOOK.md` §8 — **実アドレスを埋めた実行版**（マスターはコピペ4操作）

## マスターがやること（これだけ）

`https://app.hyperliquid-testnet.xyz/API` を開く → 右上 `Connect` → 名前 `yowayowa-exec` と
アドレス `0xc6f0c5c7aca59cd8ba4d56ed798102bc00cf66e5` を貼って行の `Connect` → ウォレットで承認。
所要1〜2分。`Generate` は押さない。秘密鍵は受け取らない。

## やっていないこと（意図的）

- `approveAgent` の送信（マスター鍵の署名が要る。VPS にマスター鍵を置かない設計）
- `/exchange` への署名リクエスト全般、発注、取消
- メインネット用の鍵生成、入金、フォーセット請求（`claimDrip` は未実行）

## 制約・注意

- エージェント鍵の権限は「発注・取消・レバレッジ変更」。**出金は不可**（プロトコル仕様）＝鍵が漏れても資金は持ち出せない。
- 承認後の完了確認は `scripts/hl_exec_phase2/check_agents.py` と `scripts/hl_exec_phase3/verify_agent_key.py`
  （どちらも `/info` のみ・鍵不要・送信なし）。
- 鍵ファイルのバックアップは取っていない（VPS 外へ複製しない方針）。再生成は `--out` を変えるか、既存を退避してから。
