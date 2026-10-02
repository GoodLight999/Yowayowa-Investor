"""Offline-only acquisition tests; synthetic fixtures never enter live data."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest


def generator_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "fetch_sec_pbr", Path(__file__).parents[1] / "scripts/fetch_sec_pbr.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_snapshot_replay_no_network_xom_history_and_prices_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = generator_module()
    calls = []
    original = tmp_path / "original_ohlcv.jsonl"
    original.write_text(json.dumps({"as_of": "2024-05-06", "close": 10, "open": 10}) + "\n")
    price_bytes = original.read_bytes()
    monkeypatch.setattr(module, "UNIVERSE", ["AAPL", "XOM", "SPY"])

    class Client:
        def __init__(self, settings: Any) -> None:
            self.client = SimpleNamespace(close=lambda: None)

        def ticker_map(self) -> Any:
            calls.append("mapping")
            return {
                "AAPL": SimpleNamespace(cik="0000000001", name="Test A"),
                "XOM": SimpleNamespace(cik="0002115436", name="Test X"),
            }

        def raw_company_facts(self, cik: str) -> Any:
            calls.append(cik)
            fact = {
                "val": 1000,
                "end": "2024-03-31",
                "filed": "2024-05-04",
                "accn": "a",
                "form": "10-Q",
            }
            equities = [fact]
            if cik == "0000034088":
                equities.append(
                    {**fact, "val": 1100, "end": "2024-04-30", "filed": "2024-05-05", "accn": "b"}
                )
            return {
                "cik": int(cik),
                "facts": {
                    "us-gaap": {"StockholdersEquity": {"units": {"USD": equities}}},
                    "dei": {
                        "EntityCommonStockSharesOutstanding": {
                            "units": {"shares": [{**fact, "val": 200}]}
                        }
                    },
                },
            }

    monkeypatch.setattr(module, "SecClient", Client)
    monkeypatch.setattr(
        module,
        "default_store",
        lambda _: SimpleNamespace(
            read=lambda *a, **kw: [json.loads(line) for line in original.read_text().splitlines()]
        ),
    )
    first = module.generate(tmp_path)
    assert first["symbols"]["XOM"]["chosen_cik"] == "0000034088"
    assert first["symbols"]["SPY"]["valid_pbr_rows"] == 0
    assert calls == ["mapping", "0000000001", "0002115436", "0000034088"]
    outputs = {p: p.read_bytes() for p in (tmp_path / "fundamentals").rglob("*") if p.is_file()}

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Network constructor must not run during cached replay")

    monkeypatch.setattr(module, "SecClient", forbidden)
    assert module.generate(tmp_path, offline=True) == first
    assert all(p.read_bytes() == content for p, content in outputs.items())
    assert original.read_bytes() == price_bytes


def test_offline_missing_cache_fails_without_network(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Missing snapshot"):
        generator_module().generate(tmp_path, offline=True)


def test_generator_404_cached_and_403_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = generator_module()
    monkeypatch.setattr(module, "UNIVERSE", ["AAPL"])
    status = [404]
    calls = []

    class Client:
        def __init__(self, settings: Any) -> None:
            self.client = SimpleNamespace(close=lambda: None)

        def ticker_map(self) -> Any:
            return {"AAPL": SimpleNamespace(cik="0000000001", name="Test")}

        def raw_company_facts(self, cik: str) -> Any:
            calls.append(cik)
            response = httpx.Response(
                status[0], request=httpx.Request("GET", "https://data.sec.gov/test")
            )
            response.raise_for_status()

    monkeypatch.setattr(module, "SecClient", Client)
    first = module.generate(tmp_path / "missing")
    assert first["symbols"]["AAPL"]["reason"] == "SEC 404"
    assert module.generate(tmp_path / "missing", offline=True) == first
    assert len(calls) == 1
    status[0] = 403
    with pytest.raises(httpx.HTTPStatusError):
        module.generate(tmp_path / "denied")
    assert not (tmp_path / "denied/fundamentals/sec-raw/CIK0000000001.json").exists()
