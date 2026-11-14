from pathlib import Path

import pytest

from yowayowa.operator_bridge.web_session import (
    BrokerWebSessionError,
    PersistentBrokerWebSession,
)


def test_broker_web_session_keeps_requests_on_configured_origin(tmp_path: Path) -> None:
    session = PersistentBrokerWebSession(
        base_url="https://broker.example/account/",
        profile_dir=tmp_path / "profile",
    )

    assert session._relative_url("orders") == "https://broker.example/account/orders"
    assert session._relative_url("/orders") == "https://broker.example/account/orders"


def test_broker_web_session_rejects_absolute_urls(tmp_path: Path) -> None:
    session = PersistentBrokerWebSession(
        base_url="https://broker.example/",
        profile_dir=tmp_path / "profile",
    )

    with pytest.raises(BrokerWebSessionError, match="relative"):
        session._relative_url("https://evil.example/steal")


def test_broker_web_session_does_not_require_browser_until_started(tmp_path: Path) -> None:
    session = PersistentBrokerWebSession(
        base_url="https://broker.example/",
        profile_dir=tmp_path / "profile",
    )

    assert session.started is False
    with pytest.raises(BrokerWebSessionError, match="not been started"):
        session.page()
