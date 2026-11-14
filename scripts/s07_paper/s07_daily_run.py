#!/usr/bin/env python3
"""S-07 ペーパー日次ランナーの cron エントリポイント（no_agent 用）。

1回の実行で「取得 → 日次ランナー → ゲート判定」を順に走らせ、
失敗が2回連続したときだけ Telegram へ通知する。成功時は静かに1行だけ返す。

  * 実弾0・注文0: 呼ぶのはHL公開API（読み取り）だけで、署名も /exchange も使わない
  * 冪等: 日次ランナー側が「同じ日を2回処理しない」を担保（state.last_processed_day）
  * 二重起動防止: ロックファイルで排他

使い方:
    python3 s07_daily_run.py                # 本番（live名前空間）
    python3 s07_daily_run.py --verbose      # 画面に詳細
    python3 s07_daily_run.py --test-notify  # Telegram通知経路の疎通確認
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = Path("/root/current-work/yowayowa-investor/data/s07_paper")
ENV_FILE = Path("/root/.hermes/.env")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _env(key: str) -> str | None:
    if os.environ.get(key):
        return os.environ[key]
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def send_telegram(text: str) -> tuple[bool, str]:
    token = _env("TELEGRAM_BOT_TOKEN")
    chat = _env("TELEGRAM_HOME_CHANNEL") or _env("TELEGRAM_ALLOWED_USERS")
    if not token or not chat:
        return False, "TELEGRAM_BOT_TOKEN / TELEGRAM_HOME_CHANNEL が無い"
    body = json.dumps(
        {"chat_id": chat, "text": text, "disable_web_page_preview": True}
    ).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
        return bool(payload.get("ok")), json.dumps(payload)[:200]
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _health_path(data_dir: Path, ns: str) -> Path:
    return data_dir / f"health_{ns}.json"


def load_health(data_dir: Path, ns: str) -> dict:
    p = _health_path(data_dir, ns)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            pass
    return {"fail_streak": 0, "last_ok": None, "last_error": None, "history": []}


def save_health(data_dir: Path, ns: str, h: dict) -> None:
    p = _health_path(data_dir, ns)
    h["history"] = h.get("history", [])[-100:]
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(h, ensure_ascii=False, indent=1))
        os.replace(tmp, p)
    except OSError:
        pass  # 状態が書けない環境でも通知だけは出す


def run_step(cmd: list[str], timeout: int = 300) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(HERE))
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _parse(argv: list[str] | None):
    ap = argparse.ArgumentParser(description="S-07 paper daily orchestrator (cron, no_agent)")
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--namespace", default="live")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--test-notify", action="store_true")
    ap.add_argument("--skip-fetch", action="store_true")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """想定外の例外でも「失敗」として数え、2回連続なら通知する。"""
    args = _parse(argv)
    data_dir = Path(args.data_dir)
    try:
        return _run(args, data_dir)
    except Exception as exc:  # noqa: BLE001
        return _record_failure(data_dir, args.namespace,
                               f"unexpected: {type(exc).__name__}: {exc}")


def _record_failure(data_dir: Path, ns: str, error: str) -> int:
    health = load_health(data_dir, ns)
    health["fail_streak"] = int(health.get("fail_streak", 0)) + 1
    health["last_error"] = f"{_now()} {error}"
    health["history"].append({"at": _now(), "event": "fail", "error": error,
                              "fail_streak": health["fail_streak"]})
    save_health(data_dir, ns, health)
    msg = (f"【S-07 ペーパー({ns})】日次ランナーが{health['fail_streak']}回連続で失敗。\n"
           f"{error}\n"
           f"解除条件: `python3 {HERE}/s07_daily_run.py --namespace {ns}` が exit 0 で終わること")
    if health["fail_streak"] >= 2:
        sent, detail = send_telegram(msg)
        health["history"].append({"at": _now(), "event": "alert", "sent": sent, "detail": detail})
        save_health(data_dir, ns, health)
        print(msg)
    else:
        print(f"FAIL(1回目・通知なし): {error}")
    return 1


def _run(args, data_dir: Path) -> int:
    health = load_health(data_dir, args.namespace)

    if args.test_notify:
        ok, detail = send_telegram(
            "【S-07 ペーパー】Telegram通知経路のテストです。"
            "日次ランナーが2回連続で失敗したときだけ、この経路で通知します（実弾0・注文0）。"
        )
        print(f"test_notify ok={ok} detail={detail}")
        health["history"].append({"at": _now(), "event": "test_notify", "ok": ok})
        save_health(data_dir, args.namespace, health)
        return 0 if ok else 1

    lock_path = data_dir / f".{args.namespace}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = open(lock_path, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("SKIP: 前回実行がまだ動いている（ロック取得失敗）")
        return 0

    # --- 【B1】HL 上位20銘柄ファンディング観測（t_00e2c1d7・2026-11-04 追記） ---
    # S-07 本体の取得/ランナー/ゲート・health・Telegram通知には一切影響させない best-effort ステップ。
    # 失敗してもこの観測専用の health（funding_top20/health.json）に記録して続行する。
    # 読み取りのみ・注文0（hl_funding_top20.py の docstring に定義式と殺す基準を明記）。
    try:
        rc_f, out_f, _err_f = run_step(
            [sys.executable, str(HERE / "hl_funding_top20.py"), "--data-dir", str(data_dir), "--json"],
            420,
        )
        if rc_f == 0 and out_f.strip():
            print(f"funding_top20 OK: {out_f.strip().splitlines()[-1]}")
        else:
            print(f"funding_top20 FAIL rc={rc_f}（S-07本体には影響なし・詳細は {data_dir}/funding_top20/health.json）")
    except Exception as exc:  # noqa: BLE001
        print(f"funding_top20 FAIL unexpected: {type(exc).__name__}: {exc}（S-07本体には影響なし）")

    # --- 【J1】円建て価格差観測（bitFlyer/bitbank vs Binance×USDJPY・カード t_aa251cce・2026-11-06 追記） ---
    # S-07 本体・B1・B2 には一切影響させない best-effort ステップ。公開APIの読み取りのみ・注文0・署名なし。
    # 失敗してもこの観測専用の health（jpy_premium/health.json）に記録して続行する。
    # 15分ループは flock 二重起動なしで確保され、毎UTC日 00:52 に自己終了する（B2のOIループと同一様式）。
    try:
        rc_j, out_j, _err_j = run_step(
            [sys.executable, str(HERE / "jpy_premium_obs.py"), "--daily-hook",
             "--data-dir", str(data_dir)],
            180,
        )
        if rc_j == 0 and out_j.strip():
            print(f"jpy_premium OK: {out_j.strip().splitlines()[-1]}")
        else:
            print(f"jpy_premium FAIL rc={rc_j}（S-07本体には影響なし・詳細は {data_dir}/jpy_premium/health.json）")
    except Exception as exc:  # noqa: BLE001
        print(f"jpy_premium FAIL unexpected: {type(exc).__name__}: {exc}（S-07本体には影響なし）")

    # --- 【項目5】前向き局面損益（凍結仕様・カード t_d4fdcfc4・2026-11-07 追記） ---
    # 既存 days/*.json には書かない（新規 funding_top20/fwd_item5/ だけに追記する）。
    # 入力は前向き収集の candles_1m/（読み取りのみ）・実弾0・入金0・注文0・署名なし。
    # S-07 本体・B1・B2・J1・health・Telegram通知には一切影響させない best-effort ステップ。
    try:
        rc_p, out_p, _err_p = run_step(
            [sys.executable, str(HERE / "fwd_item5_pnl.py"), "--daily-hook",
             "--data-dir", str(data_dir)],
            300,
        )
        if rc_p == 0 and out_p.strip():
            print(f"fwd_item5 OK: {out_p.strip().splitlines()[-1]}")
        else:
            print(f"fwd_item5 FAIL rc={rc_p}（S-07本体には影響なし・{data_dir}/funding_top20/fwd_item5/ を確認）")
    except Exception as exc:  # noqa: BLE001
        print(f"fwd_item5 FAIL unexpected: {type(exc).__name__}: {exc}（S-07本体には影響なし）")

    # --- 【項目5】判定用ワンページ生成（カード t_437a9f72・2026-11-07 追記） ---
    # 直前に更新された fwd_item5/ の確定局面だけを読み、判定ページを1枚出す（読み取り->decision_* のみ）。
    # 「データが届いた時点」で評価するため evaluated_at_utc と latency_hours を decision_ledger.json に残す。
    # 実弾0・入金0・注文0・署名なし。cron の追加・間隔変更はしない。失敗しても本体は続行する。
    try:
        rc_d, out_d, _err_d = run_step(
            [sys.executable, str(HERE / "decision_page.py"),
             "--data-dir", str(data_dir)],
            60,
        )
        if rc_d == 0 and out_d.strip():
            verdict = [ln for ln in out_d.strip().splitlines() if ln.startswith("**判定:")]
            print(f"decision_page OK: {verdict[0] if verdict else out_d.strip().splitlines()[0]}")
        else:
            print(f"decision_page FAIL rc={rc_d}（S-07本体には影響なし・"
                  f"{data_dir}/funding_top20/fwd_item5/ を確認）")
    except Exception as exc:  # noqa: BLE001
        print(f"decision_page FAIL unexpected: {type(exc).__name__}: {exc}（S-07本体には影響なし）")

    py = sys.executable
    steps = []
    if not args.skip_fetch:
        steps.append(("fetch", [py, str(HERE / "fetch_s07_data.py"), "--data-dir", str(data_dir), "--json"], 180))
    steps.append((
        "runner",
        [py, str(HERE / "s07_paper_runner.py"), "--data-dir", str(data_dir),
         "--namespace", args.namespace, "--measure-book", "--json"],
        300,
    ))
    steps.append((
        "gate",
        [py, str(HERE / "s07_gate.py"), "--data-dir", str(data_dir),
         "--namespace", args.namespace, "--json"],
        120,
    ))

    results: dict[str, dict] = {}
    error = None
    for name, cmd, tmo in steps:
        try:
            rc, out, err = run_step(cmd, tmo)
        except subprocess.TimeoutExpired:
            error = f"{name}: timeout after {tmo}s"
            break
        except Exception as exc:  # noqa: BLE001
            error = f"{name}: {exc}"
            break
        if rc != 0:
            error = f"{name}: exit {rc}: {(err or out)[:300]}"
            break
        try:
            results[name] = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
        except json.JSONDecodeError:
            results[name] = {"raw": out[:500]}
        if args.verbose:
            print(f"[{name}] rc={rc} {out[:2000]}")

    if error:
        return _record_failure(data_dir, args.namespace, error)

    health["fail_streak"] = 0
    health["last_ok"] = _now()
    health["history"].append({"at": _now(), "event": "ok"})
    save_health(data_dir, args.namespace, health)

    run = results.get("runner", {})
    gate = results.get("gate", {})
    mt = gate.get("midterm") or {}
    line = run.get("oneline")
    if not line:
        processed = run.get("processed") or []
        equity = run.get("equity", gate.get("equity"))
        total = run.get("total_pnl", gate.get("total_pnl"))
        dd = run.get("max_dd_pct")
        pf = run.get("pf")
        trades = run.get("trades", run.get("fills", gate.get("trades")))
        liq = run.get("liquidations")
        line = (
            f"S-07 paper[{args.namespace}] OK {_now()} "
            f"処理日={','.join(processed) if processed else 'なし'} "
            f"equity={equity} 累計={total} PF={pf} "
            f"DD={dd}% 取引={trades} 清算={liq} 実弾0"
        )
    if run.get("note"):
        line += f" / {run['note']}"
    if mt:
        line += f" / 中間判定={mt.get('verdict')}({mt.get('reason')})"
    line += f" / ゲート={gate.get('verdict')} / 実弾0・注文0"
    print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
