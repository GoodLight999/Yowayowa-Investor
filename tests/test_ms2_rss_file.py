"""MS2 RSS file-provider tests: JSONL ingest, dedupe, provenance, no zero-fill.

Normal, failure, and boundary paths:
- normal: latest-as-of record wins per symbol, provenance is complete;
- failure: empty store -> unavailable/Ms2RssLookupError, unparsable line
  skipped, unknown symbol unavailable;
- boundary: tie on as_of broken by retrieved_at, missing quote fields stay
  None (never zero-filled), partial store still serves readable files.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from yowayowa.config import get_settings
from yowayowa.domain import LicenseClass
from yowayowa.providers.ms2_rss_file import (
    MS2_QUOTES_DIR,
    Ms2RssFileError,
    Ms2RssFileProvider,
    Ms2RssLookupError,
)
from yowayowa.services.licensing import license_catalog, public_api_allowed, source_policy


def _record(
    symbol: str,
    as_of: str,
    retrieved_at: str,
    *,
    quotes: dict[str, float | None] | None = None,
    name: str = "テスト銘柄",
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "name": name,
        "quotes": quotes
        if quotes is not None
        else {
            "bid": 2857.0,
            "ask": 2858.5,
            "high": 2872.0,
            "low": 2841.0,
            "last": 2857.5,
            "volume": 27543100.0,
        },
        "as_of": as_of,
        "retrieved_at": retrieved_at,
    }


def _write(path: Path, *records: dict[str, object], raw_lines: list[str] | None = None) -> None:
    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    if raw_lines:
        lines.extend(raw_lines)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture()
def provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Ms2RssFileProvider:
    monkeypatch.chdir(tmp_path)
    return Ms2RssFileProvider(get_settings(), quotes_dir=tmp_path / "data" / "ms2" / "quotes")


# ------------------------------------------------------------------ normal


def test_latest_as_of_record_wins_per_symbol(provider: Ms2RssFileProvider, tmp_path: Path) -> None:
    store = provider.quotes_dir
    store.mkdir(parents=True)
    _write(
        store / "ms2_quotes_20260924.jsonl",
        _record(
            "7203",
            "2026-09-24T09:01:00+09:00",
            "2026-09-24T00:01:00+00:00",
            quotes={"last": 100.0},
        ),
        _record(
            "7203",
            "2026-09-24T15:00:00+09:00",
            "2026-09-24T06:00:00+00:00",
            quotes={"last": 123.0},
        ),
    )
    batch = provider.quotes(["7203"])

    assert batch.unavailable_symbols == []
    assert set(batch.quotes) == {"7203"}
    quote = batch.quotes["7203"]
    assert quote.price == 123.0
    assert quote.as_of == datetime(2026, 9, 24, 6, 0, tzinfo=UTC)


def test_partial_store_skips_unparsable_lines_and_others_still_served(
    provider: Ms2RssFileProvider,
) -> None:
    store = provider.quotes_dir
    store.mkdir(parents=True)
    _write(
        store / "ms2_quotes_20260924.jsonl",
        _record("7203", "2026-09-24T15:00:00+09:00", "2026-09-24T06:00:00+00:00"),
        _record("6501", "2026-09-24T15:00:00+09:00", "2026-09-24T06:00:00+00:00"),
        raw_lines=['{"symbol": "truncat', "not json at all", ""],
    )

    batch = provider.quotes(["7203", "6501"])
    assert batch.unavailable_symbols == []
    assert set(batch.quotes) == {"7203", "6501"}


def test_provenance_is_complete_and_personal_only(provider: Ms2RssFileProvider) -> None:
    store = provider.quotes_dir
    store.mkdir(parents=True)
    _write(
        store / "ms2_quotes_20260924.jsonl",
        _record("7203", "2026-09-24T15:00:00+09:00", "2026-09-24T06:00:00+00:00"),
    )
    provenance = provider.quotes(["7203"]).provenance

    assert provenance.provider == "rakuten-ms2-rss"
    assert provenance.license_class is LicenseClass.PERSONAL_ONLY
    assert provenance.source_url == "ms2-file://data/ms2/quotes"
    assert provenance.retrieved_at is not None
    assert provenance.notes


def test_licensing_registry_declares_ms2_personal_only() -> None:
    policy = source_policy("rakuten-ms2-rss")
    assert policy is not None
    assert policy.license_class is LicenseClass.PERSONAL_ONLY
    assert policy.public_api is False
    assert public_api_allowed("rakuten-ms2-rss") is False
    assert "rakuten-ms2-rss" in license_catalog("personal").blocked_sources


def test_descriptor_blocks_public_mode() -> None:
    from yowayowa.providers.base import ProviderPolicyError, enforce_provider_policy

    descriptor = Ms2RssFileProvider.__dict__["descriptor"]
    assert descriptor.license_class is LicenseClass.PERSONAL_ONLY
    with pytest.raises(ProviderPolicyError):
        enforce_provider_policy(descriptor, mode="public")


# ---------------------------------------------------------------- failure


def test_missing_symbol_is_unavailable_and_quote_raises(provider: Ms2RssFileProvider) -> None:
    batch = provider.quotes(["9999"])
    assert batch.quotes == {}
    assert batch.unavailable_symbols == ["9999"]
    with pytest.raises(Ms2RssLookupError):
        provider.quote("9999")


def test_missing_quote_fields_raise_instead_of_zero(provider: Ms2RssFileProvider) -> None:
    store = provider.quotes_dir
    store.mkdir(parents=True)
    _write(
        store / "ms2_quotes_20260924.jsonl",
        _record("7203", "2026-09-24T15:00:00+09:00", "2026-09-24T06:00:00+00:00", quotes={}),
    )
    with pytest.raises(Ms2RssLookupError):
        provider.quote("7203")


def test_unreadable_file_raises_ms2_file_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = Ms2RssFileProvider(get_settings(), quotes_dir=tmp_path)
    (tmp_path / "locked.jsonl").write_text("x", encoding="utf-8")

    real_read_text = Path.read_text

    def _deny(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == "locked.jsonl":
            raise OSError("simulated unreadable file")
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", _deny)
    with pytest.raises(Ms2RssFileError):
        provider.load_records()


# ---------------------------------------------------------------- boundary


def test_as_of_tie_broken_by_retrieved_at(provider: Ms2RssFileProvider) -> None:
    store = provider.quotes_dir
    store.mkdir(parents=True)
    _write(
        store / "a.jsonl",
        _record(
            "7203",
            "2026-09-24T15:00:00+09:00",
            "2026-09-24T05:59:00+00:00",
            quotes={"last": 1.0},
        ),
    )
    _write(
        store / "b.jsonl",
        _record(
            "7203",
            "2026-09-24T15:00:00+09:00",
            "2026-09-24T06:01:00+00:00",
            quotes={"last": 2.0},
        ),
    )
    assert provider.quote("7203").price == 2.0


def test_gap_fields_stay_none_never_zero_filled(provider: Ms2RssFileProvider) -> None:
    store = provider.quotes_dir
    store.mkdir(parents=True)
    _write(
        store / "ms2_quotes_20260924.jsonl",
        _record(
            "7203",
            "2026-09-24T15:00:00+09:00",
            "2026-09-24T06:00:00+00:00",
            quotes={
                "bid": None,
                "ask": None,
                "high": 2872.0,
                "low": 2841.0,
                "last": 2857.5,
                "volume": None,
            },
        ),
    )
    # Load-level check: no field was coerced to 0 by the receiver.
    record = provider.load_records()[0]
    assert record["quotes"] == {
        "bid": None,
        "ask": None,
        "high": 2872.0,
        "low": 2841.0,
        "last": 2857.5,
        "volume": None,
    }
    assert provider.quote("7203").price == 2857.5


def test_default_quotes_dir_constant() -> None:
    assert MS2_QUOTES_DIR == "data/ms2/quotes"


def test_windows_exporter_build_record_schema() -> None:
    """The exporter must emit exactly the transport schema the provider reads."""

    import importlib.util
    import sys
    from datetime import datetime as dt

    here = Path(__file__).resolve().parents[1]
    spec_path = here / "scripts" / "windows" / "ms2_rss_export.py"
    spec = importlib.util.spec_from_file_location("ms2_rss_export", spec_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    as_of = dt(2026, 9, 24, 15, 0, 0)
    retrieved = dt(2026, 9, 24, 6, 0, 0)
    record = module.build_record(
        "7203",
        "トヨタ自動車",
        {},
        as_of=as_of,
        retrieved_at=retrieved,
    )
    assert set(record) == {"symbol", "name", "quotes", "as_of", "retrieved_at"}
    assert set(record["quotes"]) == set(module.QUOTES_HEADER)
    assert all(value is None for value in record["quotes"].values())
    assert record["as_of"].endswith("+09:00")
    assert record["retrieved_at"].endswith("+00:00")
