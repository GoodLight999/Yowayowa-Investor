# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""S-07 オフライン入力の manifest と日足検証（設計書 §2・S07-A01）。

このモジュールは **読み取り専用の純粋検証** だけを行う。ネットワークへは触れず、
保存済みの合成応答（fixture）を検証する。オフライン fixture が証明できるのは
「fixture の前提」だけであり、provider が testnet を証明したことにはならない。
その限界は ``Provenance.fixture_only`` に明示する。

検証規則（docstring が正本）:

* ``network`` は完全一致で ``testnet``。未知・混在・行ごとの不一致は全バッチ拒否。
* ``source_url`` は testnet の ``/info`` 完全一致のみ（大文字・パス・クエリ等は拒否）。
* ``response_sha256`` は ``response`` の正規化 JSON と一致しなければ拒否
  （ラベルだけを書き換えた混入を、応答本体との結び付きで検出する）。
* バッチの ``data_kind`` は必須で ``synthetic``/``mixed``/``real`` のいずれか。
* 日足は ``t``（開始, UTC 日境界）と ``T``（終了, 最終ミリ秒）を持ち、
  ``T - t + 1 == 86_400_000`` を満たすこと。
* 確定足は ``T < as_of``。``T >= as_of`` は未確定として指標入力から除外する。
* 同一 ``(t, T)`` の完全重複は 1 回だけ採用し件数を記録、内容衝突は拒否。
* 連続する確定足が 1 日ずつでない場合は欠測として記録し、``complete=False``。
  欠測を補間・ゼロ補完しない。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from yowayowa.hl_exec.s07_offline.criteria import (
    classify_window,
    endpoint_sensitive,
    in_window,
    window_bounds_ms,
)

MS_PER_DAY = 86_400_000
INFO_URL_TESTNET = "https://api.hyperliquid-testnet.xyz/info"
ALLOWED_NETWORKS = frozenset({"testnet"})
ALLOWED_DATA_KINDS = frozenset({"synthetic", "mixed", "real"})
REQUIRED_BATCH_KEYS = (
    "network",
    "coin",
    "interval",
    "source_url",
    "retrieved_at",
    "as_of",
    "response_sha256",
    "data_kind",
    "response",
)


class ManifestRejected(ValueError):
    """バッチ全体を拒否した（未知・混在・偽装由来・応答hash不一致など）。"""


class BarRejected(ValueError):
    """個別の足が契約を満たさない（全体拒否にはせず理由を記録する場合に使う）。"""


def _parse_utc(value: Any, *, what: str) -> _dt.datetime:
    if not isinstance(value, str) or not value:
        raise ManifestRejected(f"{what} must be a non-empty UTC ISO-8601 string")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise ManifestRejected(f"{what} is not a valid timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ManifestRejected(f"{what} must include a UTC offset")
    return parsed.astimezone(_dt.UTC)


def _ms(moment: _dt.datetime) -> int:
    return int(moment.timestamp() * 1000)


def _decimal(value: Any, *, what: str) -> Decimal:
    if isinstance(value, bool):
        raise BarRejected(f"{what} must not be bool")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BarRejected(f"{what} is not a decimal: {value!r}") from exc
    if not result.is_finite():
        raise BarRejected(f"{what} must be finite: {value!r}")
    return result


def canonical_response_bytes(response: Any) -> bytes:
    """manifest 照合用の正規化 JSON バイト列（キー順固定・空白なし）。"""
    return json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def response_sha256(response: Any) -> str:
    return hashlib.sha256(canonical_response_bytes(response)).hexdigest()


@dataclass(frozen=True)
class Provenance:
    network: str
    coin: str
    interval: str
    source_url: str
    retrieved_at: str
    as_of: str
    response_sha256: str
    data_kind: str
    fixture_only: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "network": self.network,
            "coin": self.coin,
            "interval": self.interval,
            "source_url": self.source_url,
            "retrieved_at": self.retrieved_at,
            "as_of": self.as_of,
            "response_sha256": self.response_sha256,
            "data_kind": self.data_kind,
            "fixture_only": self.fixture_only,
            "fixture_only_note": (
                "an offline fixture proves only its own premise; it does not prove that the "
                "provider actually served testnet data"
            ),
        }


@dataclass(frozen=True)
class DailyBar:
    t: int
    T: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    network: str
    source_url: str

    @property
    def day_start_utc(self) -> _dt.datetime:
        return _dt.datetime.fromtimestamp(self.t / 1000, _dt.UTC)

    def as_dict(self) -> dict[str, Any]:
        return {
            "t": self.t,
            "T": self.T,
            "o": str(self.open),
            "h": str(self.high),
            "l": str(self.low),
            "c": str(self.close),
            "network": self.network,
            "source_url": self.source_url,
        }


@dataclass(frozen=True)
class ExcludedBar:
    t: int
    T: int
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"t": self.t, "T": self.T, "reason": self.reason}


@dataclass
class BarBatch:
    provenance: Provenance
    accepted: list[DailyBar] = field(default_factory=list)
    excluded: list[ExcludedBar] = field(default_factory=list)
    duplicate_count: int = 0
    duplicate_hashes: list[str] = field(default_factory=list)
    gaps: list[int] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.gaps and not self.problems

    def as_dict(self) -> dict[str, Any]:
        return {
            "provenance": self.provenance.as_dict(),
            "accepted_count": len(self.accepted),
            "accepted": [bar.as_dict() for bar in self.accepted],
            "excluded_count": len(self.excluded),
            "excluded": [bar.as_dict() for bar in self.excluded],
            "duplicate_count": self.duplicate_count,
            "duplicate_hashes": self.duplicate_hashes,
            "gaps": self.gaps,
            "problems": self.problems,
            "complete": self.complete,
        }


def _validate_batch_header(batch: Mapping[str, Any]) -> Provenance:
    missing = [key for key in REQUIRED_BATCH_KEYS if key not in batch]
    if missing:
        raise ManifestRejected(f"manifest is missing required keys: {missing}")
    network = batch["network"]
    if not isinstance(network, str) or network not in ALLOWED_NETWORKS:
        raise ManifestRejected(
            f"network must be exactly one of {sorted(ALLOWED_NETWORKS)}, got {network!r}"
        )
    source_url = batch["source_url"]
    if not isinstance(source_url, str) or source_url != INFO_URL_TESTNET:
        raise ManifestRejected(
            f"source_url must exactly equal the testnet /info endpoint {INFO_URL_TESTNET!r}, "
            f"got {source_url!r}"
        )
    data_kind = batch["data_kind"]
    if not isinstance(data_kind, str) or data_kind not in ALLOWED_DATA_KINDS:
        raise ManifestRejected(
            f"data_kind must be one of {sorted(ALLOWED_DATA_KINDS)}, got {data_kind!r}"
        )
    coin = batch["coin"]
    if not isinstance(coin, str) or not coin:
        raise ManifestRejected("coin must be a non-empty string")
    interval = batch["interval"]
    if interval != "1d":
        raise ManifestRejected(f"interval must be '1d', got {interval!r}")
    retrieved_at = _parse_utc(batch["retrieved_at"], what="retrieved_at")
    as_of_dt = _parse_utc(batch["as_of"], what="as_of")
    response = batch["response"]
    if not isinstance(response, Sequence) or isinstance(response, (str, bytes)):
        raise ManifestRejected("response must be a list of daily candles")
    declared_hash = batch["response_sha256"]
    if not isinstance(declared_hash, str) or len(declared_hash) != 64:
        raise ManifestRejected("response_sha256 must be a 64-character hex digest")
    actual_hash = response_sha256(response)
    if declared_hash.lower() != actual_hash:
        raise ManifestRejected(
            "response_sha256 does not match the stored response body "
            f"(declared={declared_hash}, actual={actual_hash}); "
            "a label-only provenance edit is rejected"
        )
    if as_of_dt < retrieved_at:
        raise ManifestRejected("as_of must not precede retrieved_at")
    return Provenance(
        network=network,
        coin=coin,
        interval="1d",
        source_url=source_url,
        retrieved_at=retrieved_at.isoformat(),
        as_of=as_of_dt.isoformat(),
        response_sha256=actual_hash,
        data_kind=data_kind,
    )


def _parse_row(row: Any, *, provenance: Provenance, as_of_ms: int) -> tuple[DailyBar, bool, str]:
    if not isinstance(row, Mapping):
        raise BarRejected(f"candle must be an object, got {type(row).__name__}")
    for key in ("t", "T", "o", "h", "l", "c"):
        if key not in row:
            raise BarRejected(f"candle is missing {key!r}")
    t = row["t"]
    T = row["T"]
    if isinstance(t, bool) or not isinstance(t, int):
        raise BarRejected(f"candle t must be an integer epoch ms, got {t!r}")
    if isinstance(T, bool) or not isinstance(T, int):
        raise BarRejected(f"candle T must be an integer epoch ms, got {T!r}")
    if t % MS_PER_DAY != 0:
        raise BarRejected(f"candle t is not on a UTC day boundary: {t}")
    if T - t + 1 != MS_PER_DAY:
        raise BarRejected(f"candle length is not one day (t={t}, T={T}, inclusive_ms={T - t + 1})")
    row_network = row.get("network")
    if row_network is not None and row_network != provenance.network:
        raise ManifestRejected(
            f"candle at t={t} declares network={row_network!r}, expected {provenance.network!r}; "
            "mixed networks are rejected for the whole batch"
        )
    o = _decimal(row["o"], what=f"candle[{t}].o")
    h = _decimal(row["h"], what=f"candle[{t}].h")
    low = _decimal(row["l"], what=f"candle[{t}].l")
    close = _decimal(row["c"], what=f"candle[{t}].c")
    if min(o, h, low, close) <= 0:
        raise BarRejected(f"candle[{t}] has non-positive OHLC")
    if not (low <= min(o, close) and h >= max(o, close) and low <= h):
        raise BarRejected(f"candle[{t}] OHLC is internally inconsistent")
    bar = DailyBar(
        t=t,
        T=T,
        open=o,
        high=h,
        low=low,
        close=close,
        network=provenance.network,
        source_url=provenance.source_url,
    )
    # 確定足 = 足の終了（最終ミリ秒）が判断時刻より厳密に前。
    confirmed = as_of_ms > T
    return bar, confirmed, "" if confirmed else "unconfirmed_or_future"


def load_batch(batch: Mapping[str, Any]) -> BarBatch:
    """manifest を検証し、確定足だけを指標入力として返す。

    バッチ全体の拒否は :class:`ManifestRejected`、個別足の除外は
    ``excluded`` に理由付きで記録する（黙って落とさない）。
    """
    provenance = _validate_batch_header(batch)
    as_of_ms = _ms(_parse_utc(batch["as_of"], what="as_of"))
    rows = batch["response"]
    result = BarBatch(provenance=provenance)

    seen: dict[tuple[int, int], DailyBar] = {}
    for raw in rows:
        bar, confirmed, reason = _parse_row(raw, provenance=provenance, as_of_ms=as_of_ms)
        if not confirmed:
            result.excluded.append(ExcludedBar(t=bar.t, T=bar.T, reason=reason))
            continue
        key = (bar.t, bar.T)
        previous = seen.get(key)
        if previous is not None:
            if previous == bar:
                result.duplicate_count += 1
                result.duplicate_hashes.append(response_sha256(bar.as_dict()))
                continue
            raise ManifestRejected(
                f"duplicate bar id (t={bar.t}, T={bar.T}) has conflicting content"
            )
        seen[key] = bar
        result.accepted.append(bar)

    result.accepted.sort(key=lambda item: item.t)
    for previous, current in zip(result.accepted, result.accepted[1:], strict=False):
        step = current.t - previous.t
        if step != MS_PER_DAY:
            if step < MS_PER_DAY:
                raise ManifestRejected(
                    f"accepted bars are not strictly daily: {previous.t} -> {current.t}"
                )
            missing_days = step // MS_PER_DAY - 1
            result.gaps.extend(previous.t + MS_PER_DAY * (i + 1) for i in range(missing_days))
            result.problems.append(
                f"{missing_days} missing day(s) between {previous.t} and {current.t}; "
                "no interpolation or zero fill is performed"
            )
    if not result.accepted:
        result.problems.append("no confirmed bars were accepted")
    return result


@dataclass(frozen=True)
class WindowPartition:
    warmup: tuple[DailyBar, ...]  # 窓前の確定足（指標用のみ）
    active: tuple[DailyBar, ...]  # 窓内 [start, end)
    post: tuple[DailyBar, ...]  # 窓後（成績に使わない）

    def as_dict(self) -> dict[str, Any]:
        return {
            "warmup_count": len(self.warmup),
            "active_count": len(self.active),
            "post_count": len(self.post),
        }


def partition_by_window(bars: Sequence[DailyBar]) -> WindowPartition:
    """確定足を窓で 3 分割する（窓外を成績へ混ぜないための境界）。"""
    start, end = window_bounds_ms()
    warmup: list[DailyBar] = []
    active: list[DailyBar] = []
    post: list[DailyBar] = []
    for bar in bars:
        if bar.t < start:
            warmup.append(bar)
        elif bar.t >= end:
            post.append(bar)
        else:
            active.append(bar)
    return WindowPartition(tuple(warmup), tuple(active), tuple(post))


def assert_decision_input(bars: Sequence[DailyBar], *, as_of_ms: int) -> None:
    """指標入力に未確定足・未来足・窓外の混入がないことを強制する。"""
    for bar in bars:
        if as_of_ms <= bar.T:
            raise BarRejected(f"unconfirmed bar at t={bar.t} reached the decision input")
        if bar.t % MS_PER_DAY != 0:
            raise BarRejected(f"bar at t={bar.t} is not UTC-day aligned")


def describe_window(time_ms: int) -> dict[str, Any]:
    """1 時刻の窓所属と端点敏感性（A09 の起票根拠に使う）。"""
    return {
        "time_ms": time_ms,
        "in_window": in_window(time_ms),
        "class": classify_window(time_ms),
        "endpoint_sensitive": endpoint_sensitive(time_ms),
    }
