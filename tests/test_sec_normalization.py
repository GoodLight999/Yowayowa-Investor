from datetime import date

from yowayowa.providers.sec import SecClient


def test_extract_metric_prefers_first_available_standard_concept() -> None:
    facts = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {
            "units": {
                "USD": [
                    {
                        "end": "2025-12-31",
                        "val": 100,
                        "fy": 2025,
                        "fp": "FY",
                        "form": "10-K",
                        "filed": "2026-02-01",
                        "accn": "a",
                    },
                    {
                        "end": "2026-03-31",
                        "val": 30,
                        "fy": 2026,
                        "fp": "Q1",
                        "form": "10-Q",
                        "filed": "2026-05-01",
                        "accn": "b",
                    },
                ]
            }
        }
    }
    series = SecClient._extract_metric(
        facts,
        "revenue",
        "Revenue",
        ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"),
    )
    assert [float(point.value) for point in series.points] == [100.0, 30.0]
    assert series.points[-1].fiscal_period == "Q1"


def test_extract_metric_merges_fallback_alias_for_later_period() -> None:
    facts = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {
            "units": {
                "USD": [
                    {
                        "start": "2025-01-01",
                        "end": "2025-12-31",
                        "val": 100,
                        "fy": 2025,
                        "fp": "FY",
                        "form": "10-K",
                        "filed": "2026-02-01",
                    }
                ]
            }
        },
        "Revenues": {
            "units": {
                "USD": [
                    {
                        "start": "2026-01-01",
                        "end": "2026-03-31",
                        "val": 30,
                        "fy": 2026,
                        "fp": "Q1",
                        "form": "10-Q",
                        "filed": "2026-05-01",
                    }
                ]
            }
        },
    }
    series = SecClient._extract_metric(
        facts,
        "revenue",
        "Revenue",
        ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"),
    )
    assert [float(point.value) for point in series.points] == [100.0, 30.0]


def test_extract_metric_prefers_quarter_only_duration_for_q2() -> None:
    facts = {
        "Revenues": {
            "units": {
                "USD": [
                    {
                        "start": "2026-01-01",
                        "end": "2026-06-30",
                        "val": 190,
                        "fy": 2026,
                        "fp": "Q2",
                        "form": "10-Q",
                        "filed": "2026-08-01",
                    },
                    {
                        "start": "2026-04-01",
                        "end": "2026-06-30",
                        "val": 100,
                        "fy": 2026,
                        "fp": "Q2",
                        "form": "10-Q",
                        "filed": "2026-08-01",
                    },
                ]
            }
        }
    }
    series = SecClient._extract_metric(facts, "revenue", "Revenue", ("Revenues",))
    assert len(series.points) == 1
    assert float(series.points[0].value) == 100.0
    assert series.points[0].period_start == date(2026, 4, 1)


def test_extract_metric_accepts_amendment_and_prefers_latest_filing() -> None:
    facts = {
        "Revenues": {
            "units": {
                "USD": [
                    {
                        "start": "2025-01-01",
                        "end": "2025-12-31",
                        "val": 100,
                        "fy": 2025,
                        "fp": "FY",
                        "form": "10-K",
                        "filed": "2026-02-01",
                    },
                    {
                        "start": "2025-01-01",
                        "end": "2025-12-31",
                        "val": 110,
                        "fy": 2025,
                        "fp": "FY",
                        "form": "10-K/A",
                        "filed": "2026-03-01",
                    },
                ]
            }
        }
    }
    series = SecClient._extract_metric(facts, "revenue", "Revenue", ("Revenues",))
    assert len(series.points) == 1
    assert float(series.points[0].value) == 110.0
    assert series.points[0].form == "10-K/A"


def test_extract_metric_ignores_non_periodic_filings() -> None:
    facts = {
        "NetIncomeLoss": {
            "units": {
                "USD": [
                    {
                        "end": "2026-01-01",
                        "val": 50,
                        "form": "8-K",
                        "filed": "2026-01-02",
                    }
                ]
            }
        }
    }
    series = SecClient._extract_metric(facts, "net_income", "Net income", ("NetIncomeLoss",))
    assert series.points == []
