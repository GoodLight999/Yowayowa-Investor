# P5 symbol 境界 証跡doc §9 訂正 — 実施記録

親: t_fe3d0ebe（worker-flash・2026-10-24 JST・VPS ubuntu）。
出典: `docs/P5_CLICK_DEPENDENCY_DECLARATION_20261024.md` §5「非スコープ・後続提案」。

## 1. 何を変えたか（当該 1 項目のみ）

対象: `docs/P5_ORDERBOOK_SYMBOL_PATH_20261024.md` §9（未達・限界）内の click 段落 1 つ。
他の節・他の文・他 doc は 1 バイトも変更していない（§3 の実測）。

### 変更前（3 行・275 bytes）

```
- `click` は pyproject に直接宣言のない typer の推移依存。service が `from click import
  ClickException` を import するため、後続で `click` の直接宣言追加を提案（本カードは
  pyproject 変更が whitelist 外のため未実施）。
```

### 変更後（1 行・233 bytes）

```
- click は pyproject.toml に直接宣言済み（click>=8.5,<9）。typer 0.27.2 は click 非依存で、click 8.5.0 を引いていたのは uvicorn 0.53.0 — 実測根拠は docs/P5_CLICK_DEPENDENCY_DECLARATION_20261024.md §1
```

## 2. diff（patch ツールが出力した統一 diff・当該段落のみ）

```diff
@@ -171,9 +171,7 @@
   「orderbook_cli.py の3コマンドで `InvalidOrderbookSymbolError` を捕捉し 1行stderr + exit 1
   （stock_cli.py の既存パターン準拠）」。whitelist の拡張を要する。
   → **解決済み（t_c3565178・§12）**。
-- `click` は pyproject に直接宣言のない typer の推移依存。service が `from click import
-  ClickException` を import するため、後続で `click` の直接宣言追加を提案（本カードは
-  pyproject 変更が whitelist 外のため未実施）。
+- click は pyproject.toml に直接宣言済み（click>=8.5,<9）。typer 0.27.2 は click 非依存で、click 8.5.0 を引いていたのは uvicorn 0.53.0 — 実測根拠は docs/P5_CLICK_DEPENDENCY_DECLARATION_20261024.md §1
 - typer の rich 枠はソース絶対path を表示する（本例外に限らず全例外の既存描画）。
   cli_shim 系（t_8262d080）が直したのは HTTP 経路の境界で、service 直呼び CLI は対象外だった。
 - API での NUL は URL として送れない（httpx が URL に制御bytesを許可しない）ため、
```

## 3. 実測（sha256・範囲・無関係変更ゼロ）

- sha256（変更前）: `4c4c4f50940a79e9b1ec59855fb45e2c03baa2a70221142727a7d4d34e8d712c`
  （対象 doc・`docs/P5_ORDERBOOK_SYMBOL_PATH_20261024.md`）
- sha256（変更後）: `d54aaffc1613de114f84816967099ae6e42e792a199118b6eeed5bb3b8b26c30`
- `wc` 実測: 変更前段落 3 行 / 275 bytes → 変更後 1 行 / 233 bytes。
- `git diff --stat`: 本カード作業中に追跡ファイル（tracked）への変更は 0。
  両 doc は untracked（`git status` の `??`）であり、着手前後で untracked 数 92 で不変
  （証跡 doc 本体の追加 1 件を除く）。無関係な整形・改行変換は無し。
- 残存文言検査: `grep -c '推移依存|直接宣言のない'`（対象 doc）→ **0 件**。

## 4. 文と実物の一致（grep 実測）

- `grep -n '"click' pyproject.toml` → `25:  "click>=8.5,<9",`
  → 変更後文の「直接宣言済み（click>=8.5,<9）」と一致。
- uv.lock 実測（awk による `[[package]]` ブロック逆引き）:
  - `name = "typer"` / `version = "0.27.2"` の dependencies は
    `annotated-doc` / `colorama`(win32) / `rich` / `shellingham` のみ → **click 非依存**。
  - click に依存するパッケージ: `uvicorn`（0.53.0）と `yowayowa-investor` 自身
    （上記 pyproject 直接宣言が依存に載ったもの）。
  → 変更後文の「typer 0.27.2 は click 非依存で、click 8.5.0 を引いていたのは
  uvicorn 0.53.0」と一致。実測根拠の原本は
  `docs/P5_CLICK_DEPENDENCY_DECLARATION_20261024.md` §1（不変・参照のみ）。

## 5. §9 他提案（CLI 1 行エラー化）との矛盾なし

§9 冒頭項目は既に「→ **解決済み（t_c3565178・§12）**」と記載があり、
本 doc §12 に実施記録（`orderbook_cli.py` try/except 1 行 stderr 化・試験更新）が実在する。
今回の差し替え文は click 直接宣言の話であり、t_c3565178 の記述と矛盾しない。

## 6. 証跡（本 doc 自体が証跡・scratch 依存なし）

上記 sha256・diff・grep・wc 出力はすべて本 doc に固定した。
本 doc も同様に untracked（commit/push は本カード禁止事項）。
