from datetime import UTC, datetime

from yowayowa.config import Settings
from yowayowa.domain import Instrument
from yowayowa.providers.sec import SecClient


def test_sec_company_facts_accepts_dot_notation_for_class_shares(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = SecClient(Settings(database_url="sqlite:///:memory:"))
    client._ticker_cache = (
        datetime.now(UTC),
        {
            "BRK-B": Instrument(
                symbol="BRK-B",
                name="Berkshire Hathaway Inc.",
                cik="0001067983",
                instrument_type="equity",
            )
        },
    )

    monkeypatch.setattr(
        client,
        "_get_json",
        lambda _url: {
            "entityName": "Berkshire Hathaway Inc.",
            "facts": {"us-gaap": {}},
        },
    )

    result = client.company_facts("BRK.B")

    assert result.symbol == "BRK.B"
    assert result.cik == "0001067983"
    assert result.company_name == "Berkshire Hathaway Inc."
