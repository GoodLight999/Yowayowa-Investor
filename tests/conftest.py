from collections.abc import Iterator
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

from yowayowa.config import get_settings
from yowayowa.db import dispose_database
from yowayowa.providers.registry import (
    edinet_client,
    frankfurter_fx_provider,
    fred_client,
    fundamentals_provider,
    sec_client,
    yahoo_deep_research_provider,
    yahoo_market_provider,
    yahoo_screener_provider,
    yahoo_tracked_calendar_provider,
)
from yowayowa.services.news import _NEWS_CACHE

_CACHED_FACTORIES = (
    get_settings,
    sec_client,
    fundamentals_provider,
    yahoo_market_provider,
    yahoo_deep_research_provider,
    yahoo_screener_provider,
    yahoo_tracked_calendar_provider,
    fred_client,
    edinet_client,
    frankfurter_fx_provider,
)


def _reset_process_state() -> None:
    dispose_database()
    _NEWS_CACHE.clear()
    for factory in _CACHED_FACTORIES:
        factory.cache_clear()
    from yowayowa.api.deps import (
        _RAKUTEN_BROWSER_SESSION,
        get_broker_read_service,
        get_private_acquisition_service,
    )

    get_private_acquisition_service.cache_clear()
    get_broker_read_service.cache_clear()
    # The operator browser session is cached at module level as well; drop it so
    # a started browser/session can never leak into (or out of) another test.
    _RAKUTEN_BROWSER_SESSION.clear()


@pytest.fixture(autouse=True)
def isolate_yowayowa_process_state(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> Iterator[None]:
    """Prevent cached providers and persistent SQLite state leaking between tests."""
    database_path = tmp_path / "yowayowa-test.db"
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{database_path}")
    _reset_process_state()
    try:
        yield
    finally:
        _reset_process_state()
