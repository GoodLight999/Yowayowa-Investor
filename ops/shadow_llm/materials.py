"""Material packet for the shadow-LLM tick (設計V2 §6 / §7).

Every number here is either computed locally from files already in the repo
(`data/crypto-ohlcv`, `data/demo-loop`) or fetched from a free public endpoint
with no API contract (Binance public futures endpoints, alternative.me F&G).
Anything that cannot be obtained is reported as ``null`` and listed in
``unavailable`` with the reason - it is never silently filled with a guess
(設計V2 §6-3).

The packet is deliberately budgeted: ``token_estimate`` keeps it inside the
2,000-token ceiling of 設計V2 §9.
"""

from __future__ import annotations

import json
import math
import statistics
import urllib.error
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

CANDIDATES = ("BTC", "ETH")
TOKEN_BUDGET = 2000
HTTP_TIMEOUT = 6.0


# --------------------------------------------------------------------------
# local readers
# --------------------------------------------------------------------------
def _read_jsonl(path: Path, limit: int | None = None) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:] if limit else rows


def _bars(root: Path, asset: str) -> list[dict]:
    rows = _read_jsonl(root / "data" / "crypto-ohlcv" / asset / "ohlcv.jsonl")
    by_day: dict[str, dict] = {}
    for row in rows:
        if row.get("interval") != "1d":
            continue
        if not all(isinstance(row.get(k), (int, float)) for k in ("open", "high", "low", "close")):
            continue
        by_day[str(row.get("as_of"))[:10]] = row
    return [by_day[k] for k in sorted(by_day)]


def _returns(bars: list[dict]) -> list[float]:
    out = []
    for prev, cur in zip(bars, bars[1:]):
        if prev["close"]:
            out.append(cur["close"] / prev["close"] - 1.0)
    return out


def _technicals(bars: list[dict]) -> dict[str, Any]:
    closes = [b["close"] for b in bars]
    out: dict[str, Any] = {}
    if len(closes) >= 200:
        sma200 = sum(closes[-200:]) / 200
        out["tech.sma200_distance"] = round(closes[-1] / sma200 - 1.0, 6)
    else:
        out["tech.sma200_distance"] = None
    window365 = closes[-365:]
    if len(window365) >= 20 and max(window365) > 0:
        out["tech.dd365"] = round(closes[-1] / max(window365) - 1.0, 6)
    else:
        out["tech.dd365"] = None

    trs: list[float] = []
    for prev, cur in zip(bars, bars[1:]):
        trs.append(max(
            cur["high"] - cur["low"],
            abs(cur["high"] - prev["close"]),
            abs(cur["low"] - prev["close"]),
        ))
    out["tech.atr14"] = round(sum(trs[-14:]) / 14.0, 4) if len(trs) >= 14 else None
    out["tech.atr14_tf"] = "1d"

    # days since the last close that broke the prior 20-bar high
    age = None
    for i in range(len(closes) - 1, 19, -1):
        if closes[i] > max(closes[i - 20:i]):
            age = len(closes) - 1 - i
            break
    out["tech.breakout_age_days"] = age

    reclaim = 0
    for prev, cur in zip(bars[-21:-1], bars[-20:]):
        if cur["low"] < prev["low"] and cur["close"] > prev["close"]:
            reclaim += 1
    out["tech.wick_reclaim20"] = reclaim

    # I1: identical close printed over consecutive daily bars
    run = 1
    best = 1
    for prev, cur in zip(bars, bars[1:]):
        if prev["close"] == cur["close"]:
            run += 1
            best = max(best, run)
        else:
            run = 1
    out["tech.stale_quotes"] = best

    last = bars[-1]
    stamp = last.get("retrieved_at") or last.get("as_of")
    try:
        age_min = (datetime.now(timezone.utc) - datetime.fromisoformat(
            str(stamp).replace("Z", "+00:00"))).total_seconds() / 60.0
        out["tech.quote_age_min"] = int(round(age_min))
    except (TypeError, ValueError):
        out["tech.quote_age_min"] = None
    out["bar_as_of"] = str(last.get("as_of"))[:10]
    out["close"] = last["close"]
    rets = _returns(bars)
    out["sigma21"] = round(statistics.pstdev(rets[-21:]), 6) if len(rets) >= 21 else None
    return out


def _book() -> dict[str, Any]:
    """Book A accounting, taken from the journal's own mtm rows.

    ``data/demo-loop/A/state.json`` is a legacy file from the v1 loop and does
    not reconcile with the journal, so it is only used when the journal has no
    mtm row at all (and the packet then reports ``degraded``).
    """
    root = Path(__file__).resolve().parents[2]
    journal = _read_jsonl(root / "data" / "demo-loop" / "journal.jsonl")
    mtm = [r for r in journal if r.get("event") == "mtm" and r.get("book") == "A"]
    fills = [r for r in journal if r.get("event") == "fill" and r.get("book") == "A"]

    state: dict[str, Any] = {}
    state_path = root / "data" / "demo-loop" / "A" / "state.json"
    if not mtm and state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    latest = mtm[-1] if mtm else {}
    equity = float(latest.get("equity") or state.get("equity") or 0.0)
    peak = float(latest.get("peak_equity") or state.get("peak") or 0.0)

    today = datetime.now(timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())

    def equity_before(day: date) -> float | None:
        for row in reversed(mtm):
            try:
                stamp = date.fromisoformat(str(row.get("mtm_date"))[:10])
            except (TypeError, ValueError):
                continue
            if stamp < day:
                return float(row.get("equity") or 0.0)
        return None

    base_today = equity_before(today)
    base_week = equity_before(week_start)
    loss_today = (equity / base_today - 1.0) if base_today else 0.0
    loss_week = (equity / base_week - 1.0) if base_week else 0.0

    out = {
        "book.drawdown": round(equity / peak - 1.0, 6) if peak else None,
        "book.equity_usd": round(equity, 6),
        "book.loss_today_pct": round(min(loss_today, 0.0), 6),
        "book.loss_week_pct": round(min(loss_week, 0.0), 6),
    }
    out["risk.headroom_usd"] = round(max(equity * 0.05 + min(equity * loss_today, 0.0), 0.0), 6)

    # M7 input: can the last mtm row explain its own equity from cash+units*price?
    recon = None
    if latest.get("prices") and latest.get("units") is not None:
        try:
            recon = float(latest.get("cash") or 0.0) + sum(
                float(latest["units"].get(a) or 0.0) * float(latest["prices"].get(a) or 0.0)
                for a in CANDIDATES
            )
        except (TypeError, ValueError):
            recon = None
    out["book.reconcile_gap_usd"] = (
        round(equity - recon, 9) if recon is not None else None
    )
    out["book.mtm_date"] = str(latest.get("mtm_date"))[:10] if latest else None

    units = latest.get("units") or state.get("units") or {}
    last_fill: dict[str, dict] = {}
    for row in fills:
        last_fill[str(row.get("asset"))] = row
    for asset in CANDIDATES:
        fill = last_fill.get(asset) or {}
        entry = fill.get("paper_fill_price")
        held = float(units.get(asset) or 0.0)
        price = float((latest.get("prices") or {}).get(asset) or 0.0)
        out[f"book.weight.{asset}"] = (
            round(held * price / equity, 6) if equity > 0 and price > 0 else 0.0
        )
        out[f"book.hold_days.{asset}"] = None
        out[f"book.entry_price.{asset}"] = entry
        if not entry or not held:
            out[f"book.mfe_mae.{asset}"] = None
            continue
        try:
            decision_date = date.fromisoformat(str(fill.get("decision_id", "").split(":")[-1]))
            out[f"book.hold_days.{asset}"] = (today - decision_date).days
        except ValueError:
            pass
        hourly = _read_jsonl(root / "data" / "demo-loop" / "A" / "1h" / f"{asset}.jsonl")
        closes = [r["close"] for r in hourly if isinstance(r.get("close"), (int, float))]
        closes = [c for c in closes if c > 0]
        if closes:
            # close-based proxy: the 1h tape has no high/low
            out[f"book.mfe_mae.{asset}"] = [
                round(max(closes) / entry - 1.0, 6),
                round(min(closes) / entry - 1.0, 6),
            ]
        else:
            out[f"book.mfe_mae.{asset}"] = None
    return out


# --------------------------------------------------------------------------
# free public endpoints (no contract, no key)
# --------------------------------------------------------------------------
def _http_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "shadow-llm/0.1"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.load(resp)


def _public_market(asset: str) -> tuple[dict[str, Any], list[str]]:
    symbol = f"{asset}USDT"
    out: dict[str, Any] = {}
    missing: list[str] = []
    try:
        prem = _http_json(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}")
        if prem.get("lastFundingRate") not in (None, ""):
            out[f"deriv.funding.{asset}"] = round(float(prem["lastFundingRate"]) * 100.0, 6)
        mark, index = prem.get("markPrice"), prem.get("indexPrice")
        if mark and index:
            out[f"deriv.basis.{asset}"] = round(float(mark) / float(index) - 1.0, 8)
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        missing.append(f"deriv.funding.{asset}={type(exc).__name__}")
    try:
        book = _http_json(f"https://fapi.binance.com/fapi/v1/ticker/bookTicker?symbol={symbol}")
        bid, ask = float(book["bidPrice"]), float(book["askPrice"])
        mid = (bid + ask) / 2.0
        out[f"exec.spread_now.{asset}"] = round((ask - bid) / mid * 1e4, 3) if mid else None
    except (urllib.error.URLError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        missing.append(f"exec.spread_now.{asset}={type(exc).__name__}")
    try:
        lsr = _http_json(
            "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
            f"?symbol={symbol}&period=1d&limit=1"
        )
        if isinstance(lsr, list) and lsr:
            out[f"deriv.lsr.{asset}"] = round(float(lsr[-1]["longShortRatio"]), 4)
    except (urllib.error.URLError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        missing.append(f"deriv.lsr.{asset}={type(exc).__name__}")
    return out, missing


def _public_sentiment() -> tuple[dict[str, Any], list[str]]:
    try:
        feed = _http_json("https://api.alternative.me/fng/?limit=1")
        return {"sent.fng": int(feed["data"][0]["value"])}, []
    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        return {}, [f"sent.fng={type(exc).__name__}"]


# --------------------------------------------------------------------------
# packet
# --------------------------------------------------------------------------
def estimate_tokens(payload: Any) -> int:
    """Conservative estimator (byte-level BPE tokens <= utf8 bytes).

    ASCII JSON runs ~3.5 bytes/token; CJK ~3.1 bytes/token.  ``bytes/2`` is
    therefore a safe upper bound for this packet's mixed content, and the
    provider's own ``usage.prompt_tokens`` is recorded alongside it.
    """
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return int(math.ceil(len(raw) / 2.0))


def build(offline: bool = False) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    now = datetime.now(timezone.utc)

    materials: dict[str, Any] = {}
    unavailable: list[str] = []
    per_asset: dict[str, dict] = {}

    for asset in CANDIDATES:
        bars = _bars(root, asset)
        tech = _technicals(bars) if bars else {}
        if not bars:
            unavailable.extend([f"tech.*.{asset}=no_bars"])
            tech = {}
        for key, value in tech.items():
            if key in {"bar_as_of", "close", "sigma21"}:
                continue
            materials[f"{key}.{asset}"] = value
            if value is None:
                unavailable.append(f"{key}.{asset}")
        per_asset[asset] = {
            "close": tech.get("close"),
            "bar_as_of": tech.get("bar_as_of"),
            "sigma21": tech.get("sigma21"),
        }

    materials.update(_book())

    # reflection-derived (memory/2026-03-04-trading-loss-reflection.md):
    # indicator releases, weekend gaps, session liquidity were the misses.
    weekday = now.weekday()
    materials["exec.weekend_gap_risk"] = weekday >= 4  # Fri decision -> Mon gap
    session = (
        "asia" if 0 <= now.hour < 7 else
        "eu" if 7 <= now.hour < 13 else
        "us" if 13 <= now.hour < 21 else
        "late_us"
    )
    materials["exec.liquidity_session"] = session
    materials["exec.utc_hour"] = now.hour
    materials["cal.minutes_to_event"] = None
    unavailable.append("cal.minutes_to_event=no_free_calendar")
    materials["news.headlines"] = None
    unavailable.append("news.headlines=no_free_news_feed")
    materials["news.topic_persistence"] = None
    unavailable.append("news.topic_persistence=no_free_news_feed")
    materials["rates.10y"] = None
    unavailable.append("rates.10y=no_free_source")
    for asset in CANDIDATES:
        materials[f"exec.spread_p95_24h.{asset}"] = None
        unavailable.append(f"exec.spread_p95_24h.{asset}=needs_24h_book_history")

    if not offline:
        for asset in CANDIDATES:
            got, missing = _public_market(asset)
            materials.update(got)
            for item in missing:
                materials.setdefault(item.split("=")[0], None)
            unavailable.extend(missing)
        sentiment, missing = _public_sentiment()
        materials.update(sentiment)
        for item in missing:
            materials.setdefault(item.split("=")[0], None)
        unavailable.extend(missing)
    else:
        for key in [f"deriv.funding.{a}" for a in CANDIDATES] + \
                   [f"deriv.basis.{a}" for a in CANDIDATES] + \
                   [f"deriv.lsr.{a}" for a in CANDIDATES] + \
                   [f"exec.spread_now.{a}" for a in CANDIDATES] + ["sent.fng"]:
            materials[key] = None
        unavailable.append("market_api=offline_flag")

    # abstain streak (book D rows; 0 today because the book is new)
    journal = _read_jsonl(root / "data" / "shadow-llm" / "journal.jsonl")
    streak = 0
    for row in reversed(journal):
        action = ((row.get("decision") or {}).get("action")) or ""
        if action == "skip":
            streak += 1
        else:
            break
    materials["abstain.consecutive"] = streak
    materials["abstain.reentry_pending"] = bool(
        journal and ((journal[-1].get("decision") or {}).get("action") == "skip")
    )

    # degraded: the materials that decide whether an entry is even allowed
    required_tech = [
        f"tech.sma200_distance.{a}" for a in CANDIDATES
    ] + [f"tech.atr14.{a}" for a in CANDIDATES] + [f"tech.stale_quotes.{a}" for a in CANDIDATES]
    degraded: list[str] = [key for key in required_tech if materials.get(key) is None]
    for asset in CANDIDATES:
        if per_asset[asset]["bar_as_of"] and per_asset[asset]["bar_as_of"] < str(now.date() - timedelta(days=2)):
            degraded.append(f"tech.bar_as_of.{asset}=stale")
    for key in (f"tech.quote_age_min.{a}" for a in CANDIDATES):
        value = materials.get(key)
        if isinstance(value, int) and value > 26 * 60:
            degraded.append(f"{key}=older_than_26h")

    packet = {
        "as_of_utc": now.isoformat(timespec="seconds"),
        "decision_date": str(now.date()),
        "candidates": list(CANDIDATES),
        "per_asset": per_asset,
        "materials": materials,
        "unavailable": sorted(unavailable),
        "degraded": sorted(set(degraded)),
    }

    # enforce the 2,000-token ceiling: drop unknown/optional (None) materials
    # first, then the least important additions, never the tech/book core.
    while estimate_tokens(packet) > TOKEN_BUDGET:
        droppable = [k for k, v in packet["materials"].items() if v is None]
        if droppable:
            packet["materials"].pop(droppable[-1])
            continue
        if packet["unavailable"]:
            packet["unavailable"].pop()
            continue
        break
    return packet


def provided_ids(packet: dict[str, Any]) -> set[str]:
    ids = set(packet["materials"].keys())
    for key in list(ids):
        if "." in key:
            ids.add(key.rsplit(".", 1)[0])
    return ids


def series(asset: str, days: int = 40) -> dict[str, list[float]]:
    """Bars for stop resolution (M1/M1b rule only; not sent to the LLM)."""
    root = Path(__file__).resolve().parents[2]
    bars = _bars(root, asset)[-days:]
    return {
        "close": [b["close"] for b in bars],
        "high": [b["high"] for b in bars],
        "low": [b["low"] for b in bars],
    }


def last_shadow_row() -> dict[str, Any] | None:
    root = Path(__file__).resolve().parents[2]
    rows = _read_jsonl(root / "data" / "shadow-llm" / "journal.jsonl")
    return rows[-1] if rows else None


def main() -> None:  # pragma: no cover - manual inspection helper
    packet = build(offline=False)
    print(json.dumps(packet, ensure_ascii=False, indent=2))
    print("token_estimate:", estimate_tokens(packet))


if __name__ == "__main__":  # pragma: no cover
    main()
