from datetime import date

from yowayowa.providers.sec import CONCEPTS, SecClient


def test_sec_current_balance_sheet_concepts_use_standard_us_gaap_tags() -> None:
    assert CONCEPTS["current_assets"] == ("Current assets", ("AssetsCurrent",))
    assert CONCEPTS["current_liabilities"] == (
        "Current liabilities",
        ("LiabilitiesCurrent",),
    )


def test_extract_current_assets_preserves_instant_period() -> None:
    facts = {
        "AssetsCurrent": {
            "units": {
                "USD": [
                    {
                        "end": "2025-12-31",
                        "val": 125,
                        "fy": 2025,
                        "fp": "FY",
                        "form": "10-K",
                        "filed": "2026-02-01",
                    }
                ]
            }
        }
    }
    series = SecClient._extract_metric(
        facts,
        "current_assets",
        "Current assets",
        ("AssetsCurrent",),
    )
    assert len(series.points) == 1
    assert series.points[0].period_start is None
    assert series.points[0].period_end == date(2025, 12, 31)
    assert float(series.points[0].value) == 125.0
