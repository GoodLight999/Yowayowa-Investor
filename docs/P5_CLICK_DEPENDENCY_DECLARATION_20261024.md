# P5 click 直接依存宣言（t_56a38208）— 実測記録

親: t_9ad92d9b（注文板ストア symbol 境界検証・review 済）。出典: `docs/P5_ORDERBOOK_SYMBOL_PATH_20261024.md` §9。

## 1. 前提の修正（重要・本カードの実測）

親 doc §9 は「click は typer の推移依存」と記載したが、**現ロックでは成立していない**。

実測（2026-10-24・VPS ubuntu / uv 0.11.23 / 共有 venv py3.12.3）:

- `typer` 0.27.2（ロック・インストール済みとも）の declared requires は
  `shellingham>=1.3.0` / `rich>=13.8.0` / `annotated-doc>=0.0.2` / `colorama; win32` のみ。
  **click への依存は存在しない**（`importlib.metadata.requires("typer")` 実測・uv.lock の
  typer ブロック dependencies 実測も同一）。
- uv.lock 内で click 8.5.0 に依存しているのは **uvicorn 0.53.0 のみ**（`click>=7.0`・
  uv.lock 全 107 パッケージ逆引き実測）。すなわち click は `serve`/`dev` extras の
  uvicorn が偶然運んできており、typer が CLI バックエンドを替えなくても
  **uvicorn 側の依存整理で core の `from click import ClickException` が崩れ得る**。
  親 doc §9 の懸念（typer 起因）より依存根拠はさらに弱かった。

## 2. 変更内容（pyproject.toml + uv.lock のみ・source 無変更）

`src/yowayowa/services/orderbook_service.py` は core（FastAPI が import）なので、
宣言先は `[project].dependencies`（cli/dev extras ではない）。

pyproject.toml（+4 行・他カードの未コミット差分 per-file-ignores 2 行は非接触で保全）:

```toml
  "python-multipart>=0.0.20,<1",
+ # Declared directly: services/orderbook_service.py imports ClickException at
+ # core level. Not anchored by typer (0.27+ no longer depends on click); it
+ # only reached the lock via uvicorn's CLI dependency (click>=7.0).
+ "click>=8.5,<9",
]
```

uv.lock（+2 行・root `[[package]] yowayowa-investor` ブロックの metadata のみ・
**パッケージ解決は 1 件も不変・107 packages**）:

```toml
 dependencies = [
     { name = "cachetools" },
+    { name = "click" },
...
 requires-dist = [
...
+    { name = "click", specifier = ">=8.5,<9" },
```

制約 `click>=8.5,<9` の根拠: 実測ロック/実インストール 8.5.0 と一致（解決不変）・
uvicorn の要求 `>=7.0` と整合・メジャー上限 `<9` は本 pyproject の既存慣行に準拠。
arelle-release を含む全 extras のロック済み依存に click 制約は他に無い（逆引き実測）。

## 3. `uv sync` を実行しなかった判断（正直な記録）

Makefile 公式手順はロック変更後の `make install`（uv sync）1 回。ただし dry-run 実測で
「Would uninstall 3 packages: et-xmlfile 2.0.0 / openpyxl 3.1.5 / yowayowa-investor（editable・
`--no-install-project` 設計上の正常動作）」と出た。**openpyxl/et-xmlfile は他カード実行中
WIP が共有 venv へ入れたもの**（ロック外）であり、実行すると並列作業を壊すため見合わせた。

代替証明（本変更に対する sync の効果はゼロであることの実測）:

- 変更前 `uv lock --locked` → `Resolved 107 packages` / rc=0（ロック整合ベースライン）
- 変更後 `uv lock` → rc=0・差分は §2 の 2 行のみ（新規インストール不要＝click 8.5.0 は
  変更前から共有 venv に実インストール済み・`import click; click.__version__ == 8.5.0` 実測）
- `uv sync --dry-run` → `Found up-to-date lockfile at: uv.lock`（rc=0）

CI は `uv sync --extra dev` をクリーン環境で実行するため、本宣言は CI 側で通常通り検証される。

## 4. 検証（host / command / rc 実測）

| 項目 | host / command | rc | 結果 |
|---|---|---|---|
| lock 整合（変更前） | VPS / `uv lock --locked` | 0 | Resolved 107 packages |
| lock 更新 | VPS / `uv lock` | 0 | Resolved 107 packages・diff 2 行のみ（§2） |
| lock 整合（変更後） | VPS / `uv sync --dry-run --extra dev --extra operator-ir --no-install-project` | 0 | Found up-to-date lockfile（実行は §3 の理由で見合わせ） |
| import 連鎖 | VPS / venv python（PYTHONPATH=src）: `from click import ClickException` + `import yowayowa.services.orderbook_service` + `import yowayowa.api.app` | 0 | click 8.5.0 / service / API app とも import 成功 |
| lint | VPS / `ruff check src/yowayowa/services/orderbook_service.py` ＋ `ruff format --check` 同 | 0 | All checks passed / already formatted |
| 型 | VPS / `mypy src/yowayowa/services/orderbook_service.py` | 0 | Success: no issues found in 1 source file |
| 対象試験 | VPS / `pytest tests/test_orderbook.py tests/test_orderbook_auth.py tests/test_orderbook_impact_provenance.py tests/test_orderbook_symbol_path_safety.py --tb=no` | 0 | collected 59 / **59 passed** in 10.42s |
| TSUKUMO 環境 import | TSUKUMO / 共有 venv（cpython 3.14.7）で同 import probe | 0 | click 8.5.0・OK |
| TSUKUMO 環境対象試験 | TSUKUMO / 同 venv で §10 と同一 3 ファイル `pytest … --tb=no` | 0 | **49 passed / 1 skipped** in 12.36s（§10 記録の 49/1 と完全一致・skip は同一の console script 実エントリ 1 件） |

TSUKUMO 手順: 親カード §10 の検証コピー `wt-p5sym-verify` を `cp -al` ハードリンククローンした
`/home/good-light/vps-work/wt-clickdep-t56a38208/` に、本カードの pyproject.toml / uv.lock のみ
差し替え（sha256 両側一致: pyproject `2d8e8b5b…` / uv.lock `80c46f7f…`）。元コピーは無変更
（同 hash 実測 `bd3ca511…` のまま）。TSUKUMO 共有ツリー本体には触っていない。

全量 pytest の TSUKUMO 再実行は省略した: 本カードの差分は pyproject/uv.lock の metadata 2 行のみで
ソース・ロック解決に変更が無く、§10 の全量結果（3072 passed / 16 failed=既存WIP）を変異させない。
根拠は §2 の lock diff 実測（パッケージ version 変更 0）に依る。

## 5. 非スコープ・後続提案

- service / routes / models / tests は一切変更していない（本カード要件）。
- 親 doc `P5_ORDERBOOK_SYMBOL_PATH_20261024.md` §9 の「typer の推移依存」文言は本 doc §1 の
  実測により不正確。親 doc は review 承認済み成果物のため本カードでは修正せず、
  **後続カード提案**: 同 doc §9 の当該 1 文を本 doc §1 への参照差し替えで訂正。
- `orderbook_cli.py` の 1 行エラー化（親 doc §9 の別提案）は未着手（本カード外）。

## 6. 証跡（scratch・24h で消える前提）

`/root/.hermes/profiles/worker-flash/cache/scratch/` 配下:
`probe_click_t56a38208.py`（版数・typer requires・lock ブロック） /
`probe_click_t56a38208_2.py`（typer 全 requires・uv.lock typer ブロック） /
`probe_click_t56a38208_3.py`（uv.lock click 逆引き全数） /
`probe_click_t56a38208_4.py`（uvicorn click>=7.0・root requires-dist） /
`probe_click_t56a38208_import.py` + 実行出力（VPS import probe） /
`p5click_pytest_vps.txt`・`p5click_pytest_vps2.txt`（VPS 対象試験）。
TSUKUMO 側: `/home/good-light/vps-work/wt-clickdep-t56a38208/p5click_pytest_tsukumo.txt`。
