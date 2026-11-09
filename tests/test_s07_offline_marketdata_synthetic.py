# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""S-07-A01（確定足のみ・混入検知）のオフライン受入試験。

データはすべて ``*_synthetic_*.json``（合成）。PASS は試験アサーションの PASS で
あり、運用成績ではない。
"""

from __future__ import annotations

import copy
import dataclasses
import json
import socket
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from yowayowa.hl_exec.s07_offline.manifest import (
    MS_PER_DAY,
    BarRejected,
    ManifestRejected,
    assert_decision_input,
    describe_window,
    load_batch,
    partition_by_window,
    response_sha256,
)

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "s07_offline_synthetic"
BARS = FIXTURES / "bars_synthetic_A01.json"
AS_OF_MS = 1794096000000  # 2026-11-08T00:00:00Z


def _fixture() -> dict[str, Any]:
    return json.loads(BARS.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _forbid_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("network access attempted during an offline S-07 test")

    monkeypatch.setattr(socket.socket, "connect", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)


def test_network_block_is_effective_positive_control() -> None:
    with pytest.raises(AssertionError, match="network access attempted"):
        socket.create_connection(("127.0.0.1", 9))


def test_confirmed_bars_only_and_unconfirmed_are_detected() -> None:
    batch = load_batch(_fixture())
    assert len(batch.accepted) == 8
    assert all(bar.T < AS_OF_MS for bar in batch.accepted)
    reasons = [item.reason for item in batch.excluded]
    assert len(batch.excluded) == 2
    assert reasons.count("unconfirmed_or_future") == 2
    assert batch.duplicate_count == 0
    assert batch.complete is True


def test_boundary_bar_ending_exactly_at_as_of_is_not_confirmed() -> None:
    fixture = _fixture()
    # 11/08 の日足は T = 11/08 23:59:59.999。as_of をその最終ミリ秒に置くと
    # 有効な 1 日足のまま「T == as_of」の境界ケースになる（T < as_of のみ確定）。
    fixture["as_of"] = "2026-11-08T23:59:59.999Z"
    batch = load_batch(fixture)
    assert len(batch.accepted) == 8
    assert all(bar.T < AS_OF_MS + MS_PER_DAY - 1 for bar in batch.accepted)
    assert any(item.t == AS_OF_MS for item in batch.excluded)
    assert len(batch.excluded) == 2


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda data: data.__setitem__("network", "mainnet"), "network must be exactly"),
        (lambda data: data.__setitem__("network", "devnet"), "network must be exactly"),
        (
            lambda data: data.__setitem__("source_url", "https://api.hyperliquid.xyz/info"),
            "source_url",
        ),
        (
            lambda data: data.__setitem__(
                "source_url", "https://api.hyperliquid-testnet.xyz/info?x=1"
            ),
            "source_url",
        ),
        (lambda data: data.__setitem__("data_kind", "production"), "data_kind must be"),
        (lambda data: data.__setitem__("response_sha256", "0" * 64), "response_sha256"),
    ],
)
def test_unknown_or_fake_provenance_is_rejected(mutate, match: str) -> None:
    fixture = _fixture()
    mutate(fixture)
    with pytest.raises(ManifestRejected, match=match):
        load_batch(fixture)


def test_label_only_edit_without_response_rebinding_is_rejected() -> None:
    fixture = _fixture()
    fixture["response"][1]["c"] = "999999"
    with pytest.raises(ManifestRejected, match="response_sha256 does not match"):
        load_batch(fixture)


def test_mixed_network_row_rejects_whole_batch() -> None:
    fixture = _fixture()
    fixture["response"][2]["network"] = "mainnet"
    fixture["response_sha256"] = response_sha256(fixture["response"])
    with pytest.raises(ManifestRejected, match="mixed networks are rejected"):
        load_batch(fixture)


def test_bar_must_be_one_utc_day() -> None:
    fixture = _fixture()
    fixture["response"][1]["t"] += 1
    fixture["response_sha256"] = response_sha256(fixture["response"])
    with pytest.raises(BarRejected, match="UTC day boundary"):
        load_batch(fixture)


def test_duplicate_bar_is_applied_once_and_conflict_is_rejected() -> None:
    fixture = _fixture()
    fixture["response"].append(copy.deepcopy(fixture["response"][2]))
    fixture["response_sha256"] = response_sha256(fixture["response"])
    batch = load_batch(fixture)
    assert batch.duplicate_count == 1
    assert len(batch.accepted) == 8

    fixture = _fixture()
    conflicting = copy.deepcopy(fixture["response"][2])
    conflicting["c"] = str(float(conflicting["c"]) + 1)
    fixture["response"].append(conflicting)
    fixture["response_sha256"] = response_sha256(fixture["response"])
    with pytest.raises(ManifestRejected, match="conflicting content"):
        load_batch(fixture)


def test_missing_day_is_detected_and_not_interpolated() -> None:
    fixture = _fixture()
    fixture["response"] = [
        bar for bar in fixture["response"] if bar["t"] != AS_OF_MS - 3 * MS_PER_DAY
    ]
    fixture["response_sha256"] = response_sha256(fixture["response"])
    batch = load_batch(fixture)
    assert batch.complete is False
    assert len(batch.gaps) == 1
    assert any("no interpolation" in problem for problem in batch.problems)
    assert all(bar.t != batch.gaps[0] for bar in batch.accepted)


def test_warmup_bars_are_indicator_only_and_window_is_half_open() -> None:
    batch = load_batch(_fixture())
    partition = partition_by_window(batch.accepted)
    assert partition.as_dict() == {"warmup_count": 4, "active_count": 4, "post_count": 0}
    # 旧 11/03 行は窓外（warmup）であり、取引・成績の active に混入しない。
    assert all(bar.t < 1793750400000 for bar in partition.warmup)
    assert all(bar.t >= 1793750400000 for bar in partition.active)
    with pytest.raises(BarRejected, match="unconfirmed bar"):
        assert_decision_input(
            [batch.accepted[0], dataclasses.replace(batch.accepted[0], T=AS_OF_MS)],
            as_of_ms=AS_OF_MS,
        )


def test_endpoint_classes_are_reported_without_changing_the_window() -> None:
    start, end = 1793750400000, 1796169600000
    assert describe_window(start)["in_window"] is True
    assert describe_window(start - 1)["class"] == "before_start"
    assert describe_window(end)["class"] == "at_or_after_end"
    assert describe_window(end)["endpoint_sensitive"] is True
