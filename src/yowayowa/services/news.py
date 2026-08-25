from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from threading import Lock
from typing import Protocol

from cachetools import TTLCache
from sqlalchemy.orm import Session

from yowayowa.calendar_models import TrackedScope
from yowayowa.domain import LicenseClass, NewsFeed, Provenance
from yowayowa.news_models import SavedNewsFeed, SavedNewsItem
from yowayowa.services.calendar import resolve_tracked_symbols


class SavedNewsProvider(Protocol):
    def news(self, query: str, limit: int = 12) -> NewsFeed: ...


_NEWS_CACHE: TTLCache[tuple[str, int], NewsFeed] = TTLCache(maxsize=1024, ttl=60)
_NEWS_CACHE_LOCK = Lock()


def _symbol_news(provider: SavedNewsProvider, symbol: str, limit: int) -> NewsFeed:
    key = (symbol, limit)
    with _NEWS_CACHE_LOCK:
        cached = _NEWS_CACHE.get(key)
        if cached is not None:
            return cached
    feed = provider.news(symbol, limit)
    with _NEWS_CACHE_LOCK:
        _NEWS_CACHE[key] = feed
    return feed


def saved_news_feed(
    session: Session,
    provider: SavedNewsProvider,
    scope: TrackedScope,
    scope_id: int | None,
    limit: int = 60,
    per_symbol_limit: int = 6,
) -> SavedNewsFeed:
    symbols = resolve_tracked_symbols(session, scope, scope_id)
    if len(symbols) > 100:
        raise ValueError(f"Saved news resolved {len(symbols)} symbols; maximum is 100")

    feeds: list[NewsFeed] = []
    unavailable: list[str] = []
    if symbols:
        workers = min(6, len(symbols))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_symbol_news, provider, symbol, per_symbol_limit): symbol
                for symbol in symbols
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    feeds.append(future.result())
                except Exception:
                    unavailable.append(symbol)

    unique: dict[str, SavedNewsItem] = {}
    for feed in feeds:
        for item in feed.items:
            symbol = item.symbol or feed.query.upper()
            key = item.url or item.id
            existing = unique.get(key)
            if existing is None:
                unique[key] = SavedNewsItem(
                    id=item.id,
                    title=item.title,
                    publisher=item.publisher,
                    published_at=item.published_at,
                    url=item.url,
                    summary=item.summary,
                    symbols=[symbol],
                )
            else:
                unique[key] = existing.model_copy(
                    update={"symbols": sorted(set(existing.symbols) | {symbol})}
                )

    floor = datetime.min.replace(tzinfo=UTC)
    items = sorted(
        unique.values(), key=lambda item: (item.published_at or floor, item.title), reverse=True
    )[:limit]
    now = datetime.now(UTC)
    return SavedNewsFeed(
        scope=scope,
        scope_id=scope_id,
        symbols=symbols,
        items=items,
        unavailable_symbols=sorted(set(unavailable)),
        provenance=Provenance(
            provider="yahoo/yfinance",
            source="Yahoo Finance saved-symbol news",
            source_url="https://finance.yahoo.com/news/",
            license_class=LicenseClass.PERSONAL_ONLY,
            retrieved_at=now,
            as_of=max((item.published_at for item in items if item.published_at), default=now),
            notes=[
                "Saved-symbol news reuses the existing Yahoo Finance search-news provider.",
                "Per-symbol feeds are cached for 60 seconds and fetched with bounded concurrency.",
                "Duplicate stories retain every related saved symbol.",
                "Unavailable symbols are reported explicitly instead of being silently omitted.",
            ],
        ),
    )
