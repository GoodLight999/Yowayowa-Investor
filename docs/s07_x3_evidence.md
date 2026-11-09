# S-07 X3 dry-run 実測（2026-11-09、未検収）

対象: worktree `agent/s07-prod-runner` HEAD `d7ad9f7` の未コミット実装。TSUKUMO-KUN の隔離コピー `/home/good-light/vps-work/sessions/t_4807e488/repo` で実行。runner.py の SHA-256 は両ホストとも `34ab6451c07acceed9cabb53edcb1160245994261f19bd296e555267740d97cc`、order_gate.py は `ece690ff1c8ae0bc88fac78ae45039d6043fc64752c63afba089a14bb145b6b8`、受入テストは `2daad5e473933fdf8053b86aaaeaafc33a1cede9a2d2bae5dec160be1f4019e1`。

## 実データ保存済み fixture による日次 CLI 2回

入力: `repo/market.json`（保存された testnet 市場応答、fixture_only=true）、`tests/fixtures/s07_authenticity/real/{data,info}`（X1 保存済みスナップショット）。2026-11-08 UTC。出力は隔離先 `.../t_4807e488/out`。X1 は保存済み入力のオフライン検証。現在の口座状態や実運用可能性の証明ではない。

```
PYTHONPATH=src <venv>/bin/python -m yowayowa.hl_exec.s07_forward.runner --day 2026-11-08 --market-json "$PWD/market.json" --out "$HOME/vps-work/sessions/t_4807e488/out" --data-dir "$PWD/tests/fixtures/s07_authenticity/real/data" --account <fixture公開アドレス> --fills-json "$PWD/tests/fixtures/s07_authenticity/real/info/userFills.json" --funding-json "$PWD/tests/fixtures/s07_authenticity/real/info/userFunding.json" --state-json "$PWD/tests/fixtures/s07_authenticity/real/info/clearinghouseState.json" --orders-json "$PWD/tests/fixtures/s07_authenticity/real/info/openOrders.json"
1回目 rc=0: status=appended, day=2026-11-08, signal=SHORT, mode=dry_run, official_result=false
2回目 rc=0: status=already_recorded, day=2026-11-08, signal=SHORT
wc -l out/ledger.jsonl: 1
cmp out/ledger.jsonl out/reports/2026-11-08.json: rc=0
out/ に orders ディレクトリなし
```

出力は隔離領域に残してあり、レポートと台帳はバイト一致。実送信器は注入せず、実注文0。日次のシグナルは取引推奨ではない。

## X1 の意図的失敗

上記と同一の CLI で新規の `.../t_4807e488/fail` を出力先にし、fills のみ存在しないファイルを指定:

```
S07_FORWARD_ABORTED: ValueError: authenticity prerequisite errored: FileNotFoundError: [Errno 2] No such file or directory: '.../real/info/does-not-exist.json'
runner_exit=2
fail/ 内: .s07-forward-isolated, lock のみ
ledger.jsonl / reports / orders はいずれも存在せず
```

決定関数の非呼出は下記の mock 検査で確認。失敗時も隔離マーカーとロックは作成されるため「一切書込なし」ではない。

## テストと RED の限界

```
PYTHONPATH=src <venv>/bin/python -m pytest tests/test_s07_runner_acceptance.py tests/test_s07_forward_runner_guards.py -q -p no:cacheprovider
.................. [100%]
18 passed in 0.93s

... pytest tests/test_s07_runner_acceptance.py::test_default_dry_run_never_calls_order_path tests/test_s07_runner_acceptance.py::test_x1_failure_stops_before_decision_ledger_and_order tests/test_s07_runner_acceptance.py::test_cli_x1_failure_returns_nonzero -v -p no:cacheprovider
各 PASSED; 3 passed in 0.36s
```

**未充足:** タスク0の `docs/s07_x3_red_log.txt` にある RED は `sender` 引数未実装の2失敗であり、フラグなし発注経路テストの RED ではない。同ログは6件成功を記録する。親カードの先行コメントには試作の故意の発注呼出を入れた RED と GREEN が報告されているが、その生ログ・対象版をこの記録では確認できていない。また受入テストは `order_path` を mock している一方、現実の `run_day` が条件付きで呼ぶ経路は `_run_order_path` であり、当該 mock の非呼出だけではその実経路の非呼出を証明しない。フラグなしでの `_run_order_path` 非呼出を直接観測する RED→GREEN 証跡は別途必要。上記 2 回の CLI 実測では orders ディレクトリは作られなかった。

## 安全・不変性の範囲

- `grep -rniE 'eth_account|private_key|signing|hyperliquid\.exchange|secret|mnemonic' src/yowayowa/hl_exec/s07_forward/ --include='*.py'` は該当なし。これは直接ソースの文字列監査であり、依存先全体の動的な鍵非接触を単独で証明しない。X1 は offline=True を強制し保存済み応答を使用。実送信器・鍵を意図的に指定していない。先行 worker の実 X1 オフライン試験では `HyperliquidExchangeClient.__init__` を例外化し成功と記録されている（`docs/evidence/s07_x3_forward_20261109/README.md`）。今回の観測範囲で実注文0・署名操作0。
- VPS worktree の前後 SHA-256: 旧 script `88f5b3d6bdd2c8e830410c9b2eb095e7a0de91c01f3a558859f913eb8e872338`、既存台帳 `c7bd598ca76553aa64dba5da63171ece10aa440748e7607b6e58ae85d47c6870`、当該ユーザー crontab 全体 `7205eb1722a2f64769c6924a80aba987be116518865380f230ddd2d71ac28cef`。前後一致。旧 script 内の停止標識も同一バイト。これら以外の全ユーザー・全プロファイルの cron を比較したわけではない。
- git push は実施していない。独立レビューは未実施。

## 追加: 実経路の直接観測と負の対照

上記「未充足」は記載当時の状態。COO裁定後、`tests/test_s07_direct_path_evidence.py` で `run_day` が実際に呼ぶ `_run_order_path` を直接ラップして計測した。REDは実装前の歴史ログではなく、テスト内で `run_day` の分岐条件を `if True` に変更した**メモリ内変異**である（実ファイルは未変更、発注関数もスタブ化）。GREENは元の実装。両方ともネットワーク接続を禁止し、入力は合成、sender は `Mock` のみ。台帳は decision 行のみ、送信回数の期待は別ファイルの resend_completed イベントと照合した。RED exit 1、GREEN exit 0。テスト対象 runner SHA `34ab6451c07acceed9cabb53edcb1160245994261f19bd296e555267740d97cc`、テスト SHA `c6198b28d4307dc52a9f03c749007dda78e3723d017ea19932c763dd992b7d52` は VPS worktree と TSUKUMO コピーで一致。

実行ディレクトリ: `/home/good-light/vps-work/sessions/t_4807e488/repo`。コマンド（各 stdout/stderr を保存）:

```sh
S07_NEGATIVE_CONTROL=1 PYTHONPATH=src ~/vps-work/venvs/yowayowa-17b215b/bin/python -m pytest tests/test_s07_direct_path_evidence.py::test_no_flag_direct_path_not_called -v -s -p no:cacheprovider --basetemp=$HOME/scratch/s07_x3_red_1571
PYTHONPATH=src ~/vps-work/venvs/yowayowa-17b215b/bin/python -m pytest tests/test_s07_direct_path_evidence.py -v -s -p no:cacheprovider --basetemp=$HOME/scratch/s07_x3_green_1571
```

RED 生ログ全文:
```text
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-8.4.2, pluggy-1.6.0 -- /home/good-light/vps-work/venvs/yowayowa-17b215b/bin/python
rootdir: /home/good-light/vps-work/sessions/t_4807e488/repo
plugins: playwright-0.9.0, base-url-2.1.0, anyio-4.15.1, cov-6.3.0, httpx-0.35.0
collecting ... collected 1 item

tests/test_s07_direct_path_evidence.py::test_no_flag_direct_path_not_called no_flag direct_path_calls=1
FAILED

=================================== FAILURES ===================================
_____________________ test_no_flag_direct_path_not_called ______________________

context = (PosixPath('/home/good-light/scratch/s07_x3_red_1571/test_no_flag_direct_path_not_c0/output'), {'account': 'synthetic'...ta_dir': PosixPath('/home/good-light/scratch/s07_x3_red_1571/test_no_flag_direct_path_not_c0/input'), 'offline': True})
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7bd2fd9086b0>

    def test_no_flag_direct_path_not_called(context, monkeypatch):
        out, auth = context
        calls = []
        original = runner._run_order_path
        def observed(*args, **kwargs):
            calls.append(1)
            if os.environ.get("S07_NEGATIVE_CONTROL") == "1":
                return {"authorized": True}
            return original(*args, **kwargs)
        monkeypatch.setattr(runner, "_run_order_path", observed)
        if os.environ.get("S07_NEGATIVE_CONTROL") == "1":
            source = textwrap.dedent(inspect.getsource(runner.run_day))
            old = "if enable_order_path and sender_callable is not None:"
            assert source.count(old) == 1
            namespace = dict(runner.__dict__)
            exec(compile(source.replace(old, "if True:"), "<negative-control-only>", "exec"), namespace)
            monkeypatch.setattr(runner, "run_day", namespace["run_day"])
        runner.run_day(day=DAY, market={}, out_dir=out, authenticity=auth)
        print(f"no_flag direct_path_calls={len(calls)}")
>       assert len(calls) == 0, f"calls={len(calls)}"
E       AssertionError: calls=1
E       assert 1 == 0
E        +  where 1 = len([1])

tests/test_s07_direct_path_evidence.py:52: AssertionError
=========================== short test summary info ============================
FAILED tests/test_s07_direct_path_evidence.py::test_no_flag_direct_path_not_called
============================== 1 failed in 0.37s ===============================
```

GREEN 生ログ全文:
```text
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-8.4.2, pluggy-1.6.0 -- /home/good-light/vps-work/venvs/yowayowa-17b215b/bin/python
rootdir: /home/good-light/vps-work/sessions/t_4807e488/repo
plugins: playwright-0.9.0, base-url-2.1.0, anyio-4.15.1, cov-6.3.0, httpx-0.35.0
collecting ... collected 3 items

tests/test_s07_direct_path_evidence.py::test_no_flag_direct_path_not_called no_flag direct_path_calls=0
PASSED
tests/test_s07_direct_path_evidence.py::test_flag_sender_and_idempotence flag first=appended second=already_recorded direct_calls=1 sender_calls=1 ledger_rows=1 completed_events=1
PASSED
tests/test_s07_direct_path_evidence.py::test_flag_without_sender_fails_closed missing_sender direct_calls=0 ledger_exists=False
PASSED

============================== 3 passed in 0.28s ===============================
```
