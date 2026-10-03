"""P1D private-source service: ingestion, idempotence, provenance, fail-closed.

Every test uses an injected fake reader; no mailbox, subprocess, or network
access happens. Fixture mail bodies use fictional companies and dates.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from yowayowa.acquisition.mailbox import MailboxAccessError, MailboxMessage
from yowayowa.acquisition.models import AcquisitionFetchState, AuthState
from yowayowa.services.private_source_service import (
    EARNINGS_ANNOUNCEMENT_KIND,
    PrivateMailboxSource,
    PrivateMessageFingerprintStore,
    PrivateSourceFetchOutcome,
    PrivateSourceRegistryStore,
    PrivateSourceService,
    body_sha256,
    mailbox_source_url,
)

ACCOUNT = "operator@example.invalid"
QUERY = "from:alerts@example.invalid subject:earnings"

BODY_TWO = "\r\n".join(
    [
        "\u25a0\u6c7a\u7b97\u30ab\u30ec\u30f3\u30c0\u30fc\uff082026/09/23\uff09\u66f4\u65b0\u9298\u67c4",
        "\u30c6\u30b9\u30c8\u30c6\u30c3\u30af(SMPL):2026/10/21",
        "\u30c6\u30b9\u30c8\u30c7\u30fc\u30bf(ALFA):2026/10/21",
    ]
)

BODY_ONE = "\r\n".join(
    [
        "\u25a0\u6c7a\u7b97\u767a\u8868\u65e5\uff082026/08/12\uff09"
        "1\u55b6\u696d\u65e5\u524d\u9298\u67c4",
        "\u30c6\u30b9\u30c8\u30de\u30a4\u30af\u30ed(6871)",
    ]
)

FIXED_NOW = datetime(2026, 9, 23, 1, 2, 3, tzinfo=UTC)


class FakeReader:
    """Scripted reader; records calls, can raise, can change body between runs."""

    def __init__(
        self,
        messages: list[MailboxMessage] | None = None,
        *,
        error: MailboxAccessError | None = None,
    ) -> None:
        self.messages = list(messages or [])
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, *, max_results: int) -> list[MailboxMessage]:
        self.calls.append((query, max_results))
        if self.error is not None:
            raise self.error
        return list(self.messages)


def message(
    message_id: str = "msg-1",
    *,
    body: str = BODY_TWO,
    subject: str = "\u6c7a\u7b97\u30ab\u30ec\u30f3\u30c0\u30fc",
    sent_at: datetime | None = None,
) -> MailboxMessage:
    return MailboxMessage(
        message_id=message_id,
        thread_id="thr-1",
        sent_at=sent_at or datetime(2026, 9, 23, 7, 0, tzinfo=UTC),
        sender="alerts@example.invalid",
        subject=subject,
        body=body,
        labels=["INBOX"],
    )


def source(source_id: str = "sample-alerts") -> PrivateMailboxSource:
    return PrivateMailboxSource(
        source_id=source_id,
        provider="sample-broker-alerts",
        account=ACCOUNT,
        kind="earnings_calendar",
        query=QUERY,
    )


def service(
    tmp_path: Path,
    reader: FakeReader,
    *,
    sources: list[PrivateMailboxSource] | None = None,
    max_messages: int = 40,
    data_dir: Path | None = None,
) -> PrivateSourceService:
    return PrivateSourceService(
        data_dir=data_dir or tmp_path,
        reader_factory=lambda _: reader,
        sources=sources if sources is not None else [source()],
        now=lambda: FIXED_NOW,
        max_messages=max_messages,
    )


def timeline_entries(tmp_path: Path, symbol: str) -> list[dict[str, Any]]:
    path = tmp_path / "private-source-timeline" / symbol / "timeline.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ------------------------------------------------------------- happy path


def test_fetch_returns_ok_with_events_and_timeline_entries(tmp_path: Path) -> None:
    reader = FakeReader([message()])
    outcome = service(tmp_path, reader).fetch("sample-alerts")
    assert outcome.fetch_state is AcquisitionFetchState.OK
    assert outcome.auth_state is AuthState.AUTHENTICATED
    assert outcome.messages_scanned == 1
    assert outcome.messages_new == 1
    assert [event.symbol for event in outcome.events] == ["SMPL", "ALFA"]
    assert outcome.new_events == 2
    assert outcome.timeline_entries == 2
    assert outcome.retrieved_at == FIXED_NOW


def test_reader_receives_the_registered_query_and_max_messages(tmp_path: Path) -> None:
    reader = FakeReader([message()])
    service(tmp_path, reader, max_messages=7).fetch("sample-alerts")
    assert reader.calls == [(QUERY, 7)]


def test_events_carry_source_message_id_subject_and_sent_at(tmp_path: Path) -> None:
    reader = FakeReader([message(message_id="abc-123")])
    outcome = service(tmp_path, reader).fetch("sample-alerts")
    for event in outcome.events:
        assert event.source_message_id == "abc-123"
        assert event.source_subject == "\u6c7a\u7b97\u30ab\u30ec\u30f3\u30c0\u30fc"
        assert event.sent_at == datetime(2026, 9, 23, 7, 0, tzinfo=UTC)
        assert event.market_provider == "sample-broker-alerts"
        assert event.kind == EARNINGS_ANNOUNCEMENT_KIND


def test_section_date_fallback_reaches_the_outcome(tmp_path: Path) -> None:
    reader = FakeReader([message(body=BODY_ONE)])
    outcome = service(tmp_path, reader).fetch("sample-alerts")
    assert [(event.symbol, event.announcement_date) for event in outcome.events] == [
        ("6871.T", date(2026, 8, 12))
    ]


def test_timeline_is_appended_per_symbol(tmp_path: Path) -> None:
    reader = FakeReader([message()])
    service(tmp_path, reader).fetch("sample-alerts")
    smpl = timeline_entries(tmp_path, "SMPL")
    alfa = timeline_entries(tmp_path, "ALFA")
    assert len(smpl) == 1
    assert len(alfa) == 1
    assert smpl[0]["kind"] == EARNINGS_ANNOUNCEMENT_KIND
    assert smpl[0]["symbol"] == "SMPL"


# ------------------------------------------------------------- idempotence


def test_second_fetch_records_no_new_events(tmp_path: Path) -> None:
    reader = FakeReader([message()])
    svc = service(tmp_path, reader)
    svc.fetch("sample-alerts")
    second = svc.fetch("sample-alerts", force_refresh=True)
    assert second.new_events == 0
    assert second.timeline_entries == 0
    assert second.messages_new == 0
    # The events observed in this run are still reported (visibility), but
    # nothing new is appended to the durable timeline.
    assert len(second.events) == 2
    assert len(timeline_entries(tmp_path, "SMPL")) == 1


def test_changed_message_body_is_treated_as_new(tmp_path: Path) -> None:
    reader = FakeReader([message()])
    svc = service(tmp_path, reader)
    svc.fetch("sample-alerts")

    reader.messages = [message(body=BODY_TWO.replace("2026/10/21", "2026/10/28"))]
    outcome = svc.fetch("sample-alerts", force_refresh=True)
    assert outcome.messages_new == 1
    assert outcome.new_events == 2
    assert len(timeline_entries(tmp_path, "SMPL")) == 2


def test_duplicate_message_ids_with_same_body_are_recorded_once(tmp_path: Path) -> None:
    reader = FakeReader([message("dup"), message("dup")])
    outcome = service(tmp_path, reader).fetch("sample-alerts")
    assert outcome.new_events == 2
    assert len(timeline_entries(tmp_path, "SMPL")) == 1


def test_fingerprint_store_round_trips(tmp_path: Path) -> None:
    store = PrivateMessageFingerprintStore(root=tmp_path / "fp")
    store.record("sample-alerts", [("m1", body_sha256("a")), ("m2", body_sha256("b"))])
    assert store.known("sample-alerts") == {
        ("m1", body_sha256("a")),
        ("m2", body_sha256("b")),
    }
    assert store.known("other-source") == set()


def test_fingerprint_store_ignores_malformed_lines(tmp_path: Path) -> None:
    store = PrivateMessageFingerprintStore(root=tmp_path / "fp")
    store.record("sample-alerts", [("m1", "sha1")])
    path = store.path("sample-alerts")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
        handle.write(json.dumps({"message_id": "m2"}) + "\n")
    assert store.known("sample-alerts") == {("m1", "sha1")}


def test_events_are_readable_after_a_service_rebuild(tmp_path: Path) -> None:
    reader = FakeReader([message()])
    service(tmp_path, reader).fetch("sample-alerts")
    rebuilt = PrivateSourceService(
        data_dir=tmp_path, reader_factory=lambda _: reader, now=lambda: FIXED_NOW
    )
    events = rebuilt.events()
    assert {event["symbol"] for event in events} == {"SMPL", "ALFA"}
    # Timeline entries survive the rebuild with their provenance intact.
    assert all(event["license_class"] == "personal_only" for event in events)
    assert {event["name"] for event in events} == {
        "\u30c6\u30b9\u30c8\u30c6\u30c3\u30af",
        "\u30c6\u30b9\u30c8\u30c7\u30fc\u30bf",
    }


def test_events_filter_by_source_id(tmp_path: Path) -> None:
    reader = FakeReader([message()])
    svc = service(tmp_path, reader)
    svc.fetch("sample-alerts")
    assert len(svc.events("sample-alerts")) == 2
    assert svc.events("other-source") == []


# ------------------------------------------------------------- fail closed


def test_auth_expired_is_not_reported_as_an_empty_success(tmp_path: Path) -> None:
    reader = FakeReader(
        error=MailboxAccessError(
            AcquisitionFetchState.AUTH_EXPIRED,
            "no TTY available for keyring file backend password prompt",
        )
    )
    outcome = service(tmp_path, reader).fetch("sample-alerts")
    assert outcome.fetch_state is AcquisitionFetchState.AUTH_EXPIRED
    assert outcome.auth_state is AuthState.UNAUTHENTICED
    assert outcome.events == []
    assert outcome.timeline_entries == 0
    assert outcome.notes == [
        "operator reauthentication required: "
        "no TTY available for keyring file backend password prompt"
    ]


def test_generic_failure_propagates_with_its_reason(tmp_path: Path) -> None:
    reader = FakeReader(
        error=MailboxAccessError(AcquisitionFetchState.FAILED, "gog: command not found")
    )
    outcome = service(tmp_path, reader).fetch("sample-alerts")
    assert outcome.fetch_state is AcquisitionFetchState.FAILED
    assert outcome.auth_state is AuthState.UNKNOWN
    assert outcome.notes == ["gog: command not found"]
    assert outcome.events == []


def test_unknown_source_id_fails_closed(tmp_path: Path) -> None:
    reader = FakeReader([message()])
    outcome = service(tmp_path, reader).fetch("ghost")
    assert outcome.fetch_state is AcquisitionFetchState.FAILED
    assert outcome.notes == ["unknown private source: ghost"]
    assert outcome.provider == ""
    assert reader.calls == []


def test_zero_messages_is_a_qualified_success(tmp_path: Path) -> None:
    reader = FakeReader([])
    outcome = service(tmp_path, reader).fetch("sample-alerts")
    assert outcome.fetch_state is AcquisitionFetchState.OK
    assert outcome.messages_scanned == 0
    assert outcome.notes == ["no messages matched the source query"]


def test_extraction_notes_are_surfaced_with_the_message_id(tmp_path: Path) -> None:
    body = "\r\n".join(
        [
            "\u25a0\u6c7a\u7b97\u30ab\u30ec\u30f3\u30c0\u30fc\uff082026/09/23\uff09\u66f4\u65b0\u9298\u67c4",
            "\u30c6\u30b9\u30c8\u4f01\u696d(NOTALETTERS):2026/10/21",
        ]
    )
    reader = FakeReader([message("m-notes", body=body)])
    outcome = service(tmp_path, reader).fetch("sample-alerts")
    assert outcome.events == []
    assert any(note.startswith("message m-notes:") for note in outcome.notes)


# --------------------------------------------------------------- provenance


def test_source_url_uses_mailbox_scheme_and_urlencoded_query(tmp_path: Path) -> None:
    reader = FakeReader([message()])
    outcome = service(tmp_path, reader).fetch("sample-alerts")
    assert outcome.source_url == mailbox_source_url(ACCOUNT, QUERY)
    assert outcome.source_url is not None
    assert outcome.source_url.startswith("mailbox://" + ACCOUNT)
    assert " " not in outcome.source_url
    assert "subject%3Aearnings" in outcome.source_url


def test_timeline_entry_carries_full_provenance(tmp_path: Path) -> None:
    reader = FakeReader([message()])
    service(tmp_path, reader).fetch("sample-alerts")
    entry = timeline_entries(tmp_path, "SMPL")[0]
    provenance = entry["provenance"]
    assert provenance["provider"] == "sample-broker-alerts"
    assert provenance["license_class"] == "personal_only"
    assert provenance["source_url"] == mailbox_source_url(ACCOUNT, QUERY)
    assert provenance["retrieved_at"] == FIXED_NOW.isoformat()
    assert entry["recorded_at"]


def test_network_exchange_records_the_mailbox_carrier(tmp_path: Path) -> None:
    reader = FakeReader([message()])
    outcome = service(tmp_path, reader).fetch("sample-alerts")
    assert len(outcome.network) == 1
    exchange = outcome.network[0]
    assert exchange.method == "SEARCH"
    assert exchange.url == mailbox_source_url(ACCOUNT, QUERY)
    assert exchange.status_code is None
    assert exchange.content_type is None
    assert exchange.size_bytes is None
    assert exchange.duration_ms is not None
    assert exchange.occurred_at == FIXED_NOW


def test_network_exchange_carries_no_headers_or_env(tmp_path: Path) -> None:
    reader = FakeReader([message()])
    outcome = service(tmp_path, reader).fetch("sample-alerts")
    dumped = json.dumps(outcome.model_dump(mode="json"))
    for forbidden in ("GOG_KEYRING", "password", "Authorization", "Cookie"):
        assert forbidden not in dumped


def test_failed_fetch_still_records_the_attempt(tmp_path: Path) -> None:
    reader = FakeReader(error=MailboxAccessError(AcquisitionFetchState.AUTH_EXPIRED, "no tty"))
    outcome = service(tmp_path, reader).fetch("sample-alerts")
    assert outcome.source_url == mailbox_source_url(ACCOUNT, QUERY)
    assert outcome.retrieved_at == FIXED_NOW
    assert len(outcome.network) == 1


# --------------------------------------------------------------- registry


def test_registry_persists_sources_across_rebuild(tmp_path: Path) -> None:
    reader = FakeReader([message()])
    svc = service(tmp_path, reader)
    svc.add_source(source("second-alerts"))
    rebuilt = PrivateSourceService(
        data_dir=tmp_path, reader_factory=lambda _: reader, now=lambda: FIXED_NOW
    )
    assert [item.source_id for item in rebuilt.list_sources()] == [
        "sample-alerts",
        "second-alerts",
    ]
    assert rebuilt.get_source("second-alerts") is not None


def test_registry_store_writes_a_sources_json(tmp_path: Path) -> None:
    store = PrivateSourceRegistryStore(root=tmp_path / "private-sources")
    store.save([source()])
    assert store.path == tmp_path / "private-sources" / "sources.json"
    assert [item.source_id for item in store.load()] == ["sample-alerts"]
    assert store.load()[0].query == QUERY


def test_registry_store_ignores_malformed_entries(tmp_path: Path) -> None:
    store = PrivateSourceRegistryStore(root=tmp_path / "private-sources")
    store.root.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps([{"source_id": "!!invalid!!"}, source().model_dump(mode="json")]),
        encoding="utf-8",
    )
    assert [item.source_id for item in store.load()] == ["sample-alerts"]


def test_registry_store_tolerates_broken_json(tmp_path: Path) -> None:
    store = PrivateSourceRegistryStore(root=tmp_path / "private-sources")
    store.root.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not-json", encoding="utf-8")
    assert store.load() == []


def test_get_source_returns_none_for_unknown(tmp_path: Path) -> None:
    reader = FakeReader([])
    assert service(tmp_path, reader).get_source("ghost") is None


def test_license_class_must_be_personal_only() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PrivateMailboxSource(
            source_id="sample-alerts",
            provider="p",
            account=ACCOUNT,
            kind="earnings_calendar",
            query=QUERY,
            license_class="official_public",
        )


def test_source_id_must_match_the_configured_pattern() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PrivateMailboxSource(
            source_id="Bad_ID!",
            provider="p",
            account=ACCOUNT,
            kind="earnings_calendar",
            query=QUERY,
        )


# ------------------------------------------------------------------ cache


def test_cache_prevents_a_second_reader_call(tmp_path: Path) -> None:
    reader = FakeReader([message()])
    svc = service(tmp_path, reader)
    svc.fetch("sample-alerts")
    svc.fetch("sample-alerts")
    assert len(reader.calls) == 1


def test_force_refresh_bypasses_the_cache(tmp_path: Path) -> None:
    reader = FakeReader([message()])
    svc = service(tmp_path, reader)
    svc.fetch("sample-alerts")
    svc.fetch("sample-alerts", force_refresh=True)
    assert len(reader.calls) == 2


def test_cached_outcome_is_a_copy_not_a_shared_reference(tmp_path: Path) -> None:
    reader = FakeReader([message()])
    svc = service(tmp_path, reader)
    first = svc.fetch("sample-alerts")
    first.notes.append("mutated by caller")
    second = svc.fetch("sample-alerts")
    assert "mutated by caller" not in second.notes


def test_cache_does_not_serve_a_failed_outcome_as_success(tmp_path: Path) -> None:
    reader = FakeReader(error=MailboxAccessError(AcquisitionFetchState.AUTH_EXPIRED, "no tty"))
    svc = service(tmp_path, reader)
    first = svc.fetch("sample-alerts")
    assert first.fetch_state is AcquisitionFetchState.AUTH_EXPIRED
    # A failure is retried rather than cached: the operator may fix auth and
    # immediately re-run without --force-refresh.
    reader.error = None
    reader.messages = [message()]
    second = svc.fetch("sample-alerts")
    assert second.fetch_state is AcquisitionFetchState.OK
    assert len(reader.calls) == 2


# ----------------------------------------------------------------- events


def test_events_sorted_by_announcement_date_descending(tmp_path: Path) -> None:
    reader = FakeReader(
        [
            message("m1", body=BODY_ONE),
            message("m2", body=BODY_TWO),
        ]
    )
    svc = service(tmp_path, reader)
    svc.fetch("sample-alerts")
    dates = [event["announcement_date"] for event in svc.events()]
    assert dates == sorted(dates, reverse=True)


def test_events_limit_is_respected(tmp_path: Path) -> None:
    reader = FakeReader([message()])
    svc = service(tmp_path, reader)
    svc.fetch("sample-alerts")
    assert len(svc.events(limit=1)) == 1


def test_events_carry_license_class_and_provider(tmp_path: Path) -> None:
    reader = FakeReader([message()])
    svc = service(tmp_path, reader)
    svc.fetch("sample-alerts")
    event = svc.events()[0]
    assert event["license_class"] == "personal_only"
    assert event["market_provider"] == "sample-broker-alerts"
    assert event["source_url"] == mailbox_source_url(ACCOUNT, QUERY)


def test_events_empty_when_nothing_recorded(tmp_path: Path) -> None:
    reader = FakeReader([])
    svc = service(tmp_path, reader)
    assert svc.events() == []


def test_outcome_model_carries_cache_ttl(tmp_path: Path) -> None:
    reader = FakeReader([message()])
    outcome = service(tmp_path, reader).fetch("sample-alerts")
    assert outcome.cache_ttl_seconds == 900
    assert isinstance(outcome, PrivateSourceFetchOutcome)
