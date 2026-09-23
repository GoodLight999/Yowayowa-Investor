from __future__ import annotations

from pydantic import BaseModel, Field

from yowayowa.acquisition.auth import HeuristicAuthDetector
from yowayowa.acquisition.models import (
    AcquisitionFetchState,
    AcquisitionOutcome,
    AuthState,
    CacheStatus,
    FreshnessPolicy,
    NetworkExchange,
    SnapshotDiff,
    SnapshotRecord,
)
from yowayowa.acquisition.registry import (
    ConnectorDefinition,
    ConnectorRuntime,
)
from yowayowa.acquisition.service import PrivateAcquisitionService
from yowayowa.broker.session_notify import SessionExpiryNotifier
from yowayowa.broker_models import BrokerAccountSnapshot, BrokerOrder, BrokerPosition
from yowayowa.operator_bridge.rakuten_web import (
    RAKUTEN_SECURITIES_BROKER,
    RAKUTEN_WEB_BASE_URL,
    RAKUTEN_WEB_CONNECTOR_ID,
    RAKUTEN_WEB_HTML_CONNECTOR_ID,
    RAKUTEN_WEB_RESOURCE_CATALOG,
    RakutenResourceEntry,
    extract_fees_with_notes,
    extract_margin_state_with_notes,
    extract_symbol_names,
    lookup_rakuten_resource,
    normalize_account_with_notes,
    normalize_executions,
    normalize_open_orders,
    normalize_order_history,
    normalize_positions,
    rakuten_connector_id_for,
    summarize_payload,
)
from yowayowa.private_connectors import PrivateAcquisitionMethod

"""Broker read-side orchestration on top of PrivateAcquisitionService (P1B).

The service registers the Rakuten web connector definitions (one JSON-parser
definition and one HTML-tables definition; the per-resource catalog decides
which one serves a given resource) and reuses the acquisition pipeline for
transport, auth detection, cache, snapshots, and provenance. Read-only by
construction: no state-changing request path exists on this surface.
"""

_RAKUTEN_FRESHNESS = FreshnessPolicy(ttl_seconds=60, max_stale_seconds=600)
_RAKUTEN_DETECTOR = HeuristicAuthDetector(
    login_url_markers=("login", "signin", "sign-in"),
)


class BrokerReadOutcome(BaseModel):
    """Normalized read outcome with full acquisition provenance attached."""

    connector_id: str
    resource: str
    market: str
    fetch_state: AcquisitionFetchState
    auth_state: AuthState
    account: BrokerAccountSnapshot | None = None
    positions: list[BrokerPosition] = Field(default_factory=list)
    orders: list[BrokerOrder] = Field(default_factory=list)
    detail: dict[str, object] | None = None
    source_url: str | None = None
    retrieved_at: str | None = None
    as_of: str | None = None
    parser_version: str | None = None
    schema_version: str | None = None
    snapshot: SnapshotRecord | None = None
    diff: SnapshotDiff | None = None
    cache: CacheStatus | None = None
    network: list[NetworkExchange] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class BrokerReadService:
    """Thin orchestration layer composing PrivateAcquisitionService for broker reads."""

    def __init__(
        self,
        *,
        acquisition: PrivateAcquisitionService,
        expiry_notifier: SessionExpiryNotifier | None = None,
    ) -> None:
        self._acquisition = acquisition
        self._expiry_notifier = expiry_notifier
        # Rakuten's legitimate URLs can contain "auth"; the default detector's
        # "auth" URL marker would misclassify them as login pages. Use a
        # Rakuten-specific detector (login/signin/sign-in + login text markers).
        self._acquisition._detector = _RAKUTEN_DETECTOR
        self._register_rakuten_definitions()

    # ------------------------------------------------------------- registry

    def _register_rakuten_definitions(self) -> None:
        auth_recheck = RAKUTEN_WEB_RESOURCE_CATALOG[("account", "jp")].url
        common_notes = [
            "read-only web-session connector; catalog URLs are unverified "
            "initial assumptions (see docs/RAKUTEN_WEB_SESSION.md)",
        ]
        self._acquisition.register_connector(
            ConnectorDefinition(
                id=RAKUTEN_WEB_CONNECTOR_ID,
                provider=RAKUTEN_SECURITIES_BROKER,
                base_url=RAKUTEN_WEB_BASE_URL,
                method=PrivateAcquisitionMethod.AUTHENTICATED_WEB_SESSION,
                parser="json",
                freshness=_RAKUTEN_FRESHNESS,
                auth_recheck_resource=auth_recheck,
                notes=common_notes,
            )
        )
        self._acquisition.register_connector(
            ConnectorDefinition(
                id=RAKUTEN_WEB_HTML_CONNECTOR_ID,
                provider=RAKUTEN_SECURITIES_BROKER,
                base_url=RAKUTEN_WEB_BASE_URL,
                method=PrivateAcquisitionMethod.AUTHENTICATED_WEB_SESSION,
                parser="tables",
                freshness=_RAKUTEN_FRESHNESS,
                auth_recheck_resource=auth_recheck,
                notes=common_notes,
            )
        )

    def list_connectors(self) -> list[ConnectorRuntime]:
        """User-facing view: the single rakuten-web connector (html twin hidden)."""
        return [
            runtime
            for runtime in self._acquisition.list_connectors()
            if runtime.definition.id != RAKUTEN_WEB_HTML_CONNECTOR_ID
        ]

    def get_connector(self, connector_id: str) -> ConnectorRuntime | None:
        if connector_id == RAKUTEN_WEB_HTML_CONNECTOR_ID:
            return None
        return self._acquisition.get_connector(connector_id)

    # ---------------------------------------------------------------- fetch

    def auth_check(self, connector_id: str = RAKUTEN_WEB_CONNECTOR_ID) -> AcquisitionOutcome:
        outcome = self._acquisition.auth_check(connector_id)
        self._notify_on_outcome(outcome, connector_id)
        return outcome

    def fetch(
        self,
        resource: str,
        market: str,
        *,
        force_refresh: bool = False,
    ) -> BrokerReadOutcome:
        entry = lookup_rakuten_resource(resource, market)
        if entry is None:
            return BrokerReadOutcome(
                connector_id=RAKUTEN_WEB_CONNECTOR_ID,
                resource=resource,
                market=market,
                fetch_state=AcquisitionFetchState.FAILED,
                auth_state=AuthState.UNKNOWN,
                notes=[f"unknown resource/market: {resource}/{market}"],
            )
        connector_id = rakuten_connector_id_for(entry)
        outcome = self._acquisition.fetch(
            connector_id,
            entry.url,
            force_refresh=force_refresh,
        )
        self._notify_on_outcome(outcome, connector_id)
        return self._normalize_outcome(outcome, resource=resource, market=market, entry=entry)

    def snapshots(
        self, connector_id: str, resource: str, market: str, limit: int = 20
    ) -> list[SnapshotRecord]:
        entry = lookup_rakuten_resource(resource, market)
        if entry is None:
            return []
        return self._acquisition.snapshots(rakuten_connector_id_for(entry), entry.url, limit=limit)

    def diff(self, connector_id: str, resource: str, market: str) -> SnapshotDiff | None:
        entry = lookup_rakuten_resource(resource, market)
        if entry is None:
            return None
        return self._acquisition.diff(rakuten_connector_id_for(entry), entry.url)

    # ------------------------------------------------------------ internals

    def _notify_on_outcome(self, outcome: AcquisitionOutcome, connector_id: str) -> None:
        """Best-effort session-expiry notification on a read outcome.

        AUTH_EXPIRED fires notify_session_expired; a fully authenticated
        outcome clears the suppression entry. The notifier already swallows
        its own errors; this second guard keeps the notification strictly
        best-effort so the outcome itself is never altered.
        """

        if self._expiry_notifier is None:
            return
        try:
            if outcome.fetch_state == AcquisitionFetchState.AUTH_EXPIRED:
                self._expiry_notifier.notify_session_expired(
                    source="broker-read", detail=connector_id
                )
            elif outcome.fetch_state == AcquisitionFetchState.OK:
                self._expiry_notifier.notify_authenticated(connector_id)
        except Exception:
            # Double defense: never let a notification problem change the
            # read outcome.
            return

    def _normalize_outcome(
        self,
        outcome: AcquisitionOutcome,
        *,
        resource: str,
        market: str,
        entry: RakutenResourceEntry,
    ) -> BrokerReadOutcome:
        result = BrokerReadOutcome(
            connector_id=RAKUTEN_WEB_CONNECTOR_ID,
            resource=resource,
            market=market,
            fetch_state=outcome.fetch_state,
            auth_state=outcome.auth_state,
            source_url=outcome.source_url,
            retrieved_at=outcome.retrieved_at.isoformat() if outcome.retrieved_at else None,
            as_of=outcome.as_of.isoformat() if outcome.as_of else None,
            parser_version=outcome.parser_version,
            schema_version=outcome.schema_version,
            snapshot=outcome.snapshot,
            diff=outcome.diff,
            cache=outcome.cache,
            network=list(outcome.network),
            notes=list(outcome.notes),
        )
        payload = outcome.payload
        if payload is None:
            return result
        detail: dict[str, object] = {
            "parser_version": entry.parser_version,
            "schema_version": entry.schema_version,
            "verified": entry.verified,
            "payload_summary": summarize_payload(payload),
        }
        notes = result.notes
        if resource == "account":
            result.account, extra = normalize_account_with_notes(payload, market=market)
            notes.extend(extra)
        elif resource == "positions":
            result.positions, extra = normalize_positions(payload, market=market)
            notes.extend(extra)
        elif resource == "open_orders":
            result.orders, extra = normalize_open_orders(payload, market=market)
            notes.extend(extra)
        elif resource == "order_history":
            result.orders, extra = normalize_order_history(payload, market=market)
            notes.extend(extra)
        elif resource == "executions":
            result.orders, extra = normalize_executions(payload, market=market)
            notes.extend(extra)
        fees, fee_notes = extract_fees_with_notes(payload)
        notes.extend(fee_notes)
        if fees is not None:
            detail["fees"] = fees
        margin_state, margin_notes = extract_margin_state_with_notes(payload, market=market)
        notes.extend(margin_notes)
        if margin_state is not None:
            detail["margin_state"] = margin_state
        names = extract_symbol_names(payload)
        if names:
            detail["symbol_names"] = names
        result.detail = detail
        result.notes = notes
        return result
