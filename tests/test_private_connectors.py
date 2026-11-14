from datetime import UTC, datetime

from yowayowa.private_connectors import (
    PrivateAcquisitionMethod,
    PrivateConnectorDescriptor,
    PrivateConnectorResult,
)


def test_private_connector_metadata_keeps_public_redistribution_separate() -> None:
    descriptor = PrivateConnectorDescriptor(
        id="fixture-private-json",
        provider="fixture-broker",
        method=PrivateAcquisitionMethod.PRIVATE_HTTP,
        authenticated=True,
        redistributable=False,
        parser_version="2026-09-21",
    )
    result = PrivateConnectorResult(
        descriptor=descriptor,
        source_url="https://example.invalid/account/state",
        retrieved_at=datetime(2026, 9, 21, tzinfo=UTC),
        payload={"positions": []},
    )

    assert result.descriptor.authenticated is True
    assert result.descriptor.redistributable is False
    assert result.descriptor.method is PrivateAcquisitionMethod.PRIVATE_HTTP
