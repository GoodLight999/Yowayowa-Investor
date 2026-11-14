from datetime import UTC, datetime, timedelta

from yowayowa.acquisition.cache import AcquisitionCache, CacheEntry, evaluate_freshness
from yowayowa.acquisition.models import AcquisitionFetchState, FreshnessPolicy


def _entry(fetched_at: datetime, ttl: int) -> CacheEntry:
    return CacheEntry(
        fetched_at=fetched_at,
        expires_at=fetched_at + timedelta(seconds=ttl),
        payload_sha256="abc",
        payload={"k": "v"},
    )


def _policy(ttl: int = 900, max_stale: int = 86400) -> FreshnessPolicy:
    return FreshnessPolicy(ttl_seconds=ttl, max_stale_seconds=max_stale)


def test_fresh_hit_inside_ttl() -> None:
    now = datetime(2026, 9, 23, tzinfo=UTC)
    entry = _entry(now, ttl=900)
    status = evaluate_freshness(entry, _policy(), now + timedelta(seconds=899))
    assert status.state == AcquisitionFetchState.OK
    assert status.payload_sha256 == "abc"


def test_exactly_at_ttl_is_stale() -> None:
    now = datetime(2026, 9, 23, tzinfo=UTC)
    entry = _entry(now, ttl=900)
    status = evaluate_freshness(entry, _policy(), now + timedelta(seconds=900))
    assert status.state == AcquisitionFetchState.STALE
    assert status.reason is not None


def test_one_second_after_ttl_but_within_max_stale_is_stale() -> None:
    now = datetime(2026, 9, 23, tzinfo=UTC)
    entry = _entry(now, ttl=900)
    status = evaluate_freshness(entry, _policy(), now + timedelta(seconds=901))
    assert status.state == AcquisitionFetchState.STALE
    assert status.reason is not None


def test_past_max_stale_drops_entry() -> None:
    now = datetime(2026, 9, 23, tzinfo=UTC)
    entry = _entry(now, ttl=900)
    status = evaluate_freshness(entry, _policy(max_stale=1000), now + timedelta(seconds=1901))
    assert status.state == AcquisitionFetchState.FAILED
    assert status.reason is not None and "dropped" in status.reason


def test_cache_miss_reason() -> None:
    cache = AcquisitionCache()
    status, payload = cache.get("conn", "res", _policy())
    assert status.state == AcquisitionFetchState.FAILED
    assert status.reason == "cache-miss"
    assert payload is None


def test_cache_put_then_get_returns_payload() -> None:
    cache = AcquisitionCache()
    now = datetime(2026, 9, 23, tzinfo=UTC)
    cache.put("conn", "res", {"a": 1}, _policy(), now=now)
    status, payload = cache.get("conn", "res", _policy(), now=now + timedelta(seconds=1))
    assert status.state == AcquisitionFetchState.OK
    assert payload == {"a": 1}


def test_cache_stale_entry_still_served_with_reason() -> None:
    cache = AcquisitionCache()
    now = datetime(2026, 9, 23, tzinfo=UTC)
    cache.put("conn", "res", {"a": 1}, _policy(ttl=10), now=now)
    status, payload = cache.get("conn", "res", _policy(ttl=10), now=now + timedelta(seconds=20))
    assert status.state == AcquisitionFetchState.STALE
    assert status.reason is not None
    assert payload == {"a": 1}


def test_cache_drop_after_max_stale_then_miss() -> None:
    cache = AcquisitionCache()
    now = datetime(2026, 9, 23, tzinfo=UTC)
    policy = FreshnessPolicy(ttl_seconds=10, max_stale_seconds=30)
    cache.put("conn", "res", {"a": 1}, policy, now=now)
    status, payload = cache.get("conn", "res", policy, now=now + timedelta(seconds=100))
    assert status.state == AcquisitionFetchState.FAILED
    assert payload is None
    followup, followup_payload = cache.get("conn", "res", policy, now=now + timedelta(seconds=101))
    assert followup.reason == "cache-miss"
    assert followup_payload is None


def test_invalidate_forces_miss() -> None:
    cache = AcquisitionCache()
    now = datetime(2026, 9, 23, tzinfo=UTC)
    policy = _policy()
    cache.put("conn", "res", {"a": 1}, policy, now=now)
    cache.invalidate("conn", "res")
    status, payload = cache.get("conn", "res", policy, now=now)
    assert status.reason == "cache-miss"
    assert payload is None


def test_ttl_zero_expires_immediately() -> None:
    now = datetime(2026, 9, 23, tzinfo=UTC)
    entry = _entry(now, ttl=0)
    status = evaluate_freshness(entry, _policy(ttl=0), now + timedelta(seconds=1))
    assert status.state == AcquisitionFetchState.STALE
