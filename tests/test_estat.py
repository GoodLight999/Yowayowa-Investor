from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from yowayowa.config import Settings
from yowayowa.domain import LicenseClass
from yowayowa.providers.estat import EstatClient


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeHttpClient:
    def __init__(self, payloads: dict[str, dict[str, Any]]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, params: dict[str, object]) -> FakeResponse:
        self.calls.append((url, params))
        endpoint = url.rsplit("/", 1)[-1]
        return FakeResponse(self.payloads[endpoint])


def _result() -> dict[str, object]:
    return {
        "STATUS": 0,
        "ERROR_MSG": "正常に終了しました。",
        "DATE": "2026-08-18T12:00:00+09:00",
    }


def _table() -> dict[str, object]:
    return {
        "@id": "0003427113",
        "STAT_NAME": {"@code": "00200573", "$": "消費者物価指数"},
        "GOV_ORG": {"@code": "00200", "$": "総務省"},
        "STATISTICS_NAME": "2025年基準消費者物価指数 全国",
        "TITLE": {"@no": "1", "$": "中分類指数(全国)"},
        "CYCLE": "月次",
        "SURVEY_DATE": "202607",
        "OPEN_DATE": "2026-08-18",
        "COLLECT_AREA": "全国",
        "MAIN_CATEGORY": {"@code": "07", "$": "物価"},
        "SUB_CATEGORY": {"@code": "01", "$": "消費者物価指数"},
        "OVERALL_TOTAL_NUMBER": 3,
        "UPDATED_DATE": "2026-08-18",
    }


def fixture_payloads() -> dict[str, dict[str, Any]]:
    table = _table()
    class_inf = {
        "CLASS_OBJ": [
            {
                "@id": "tab",
                "@name": "表章項目",
                "CLASS": {
                    "@code": "1",
                    "@name": "指数",
                    "@level": "1",
                    "@unit": "2025年=100",
                },
            },
            {
                "@id": "cat01",
                "@name": "品目",
                "CLASS": [
                    {"@code": "0001", "@name": "総合", "@level": "1"},
                    {"@code": "0002", "@name": "生鮮食品を除く総合", "@level": "1"},
                ],
            },
            {
                "@id": "area",
                "@name": "地域",
                "CLASS": {"@code": "00000", "@name": "全国", "@level": "1"},
            },
            {
                "@id": "time",
                "@name": "時間軸(月次)",
                "CLASS": {
                    "@code": "2026070000",
                    "@name": "2026年7月",
                    "@level": "1",
                },
            },
        ]
    }
    return {
        "getStatsList": {
            "GET_STATS_LIST": {
                "RESULT": _result(),
                "DATALIST_INF": {
                    "NUMBER": 1,
                    "RESULT_INF": {"FROM_NUMBER": 1, "TO_NUMBER": 1},
                    "TABLE_INF": table,
                },
            }
        },
        "getMetaInfo": {
            "GET_META_INFO": {
                "RESULT": _result(),
                "METADATA_INF": {"TABLE_INF": table, "CLASS_INF": class_inf},
            }
        },
        "getStatsData": {
            "GET_STATS_DATA": {
                "RESULT": _result(),
                "STATISTICAL_DATA": {
                    "RESULT_INF": {"TOTAL_NUMBER": 3, "FROM_NUMBER": 1, "TO_NUMBER": 3},
                    "TABLE_INF": table,
                    "CLASS_INF": class_inf,
                    "DATA_INF": {
                        "NOTE": {"@char": "-", "$": "該当数値なし"},
                        "ANNOTATION": {"@annotation": "A1", "$": "fixture annotation"},
                        "VALUE": [
                            {
                                "@tab": "1",
                                "@cat01": "0001",
                                "@area": "00000",
                                "@time": "2026070000",
                                "@unit": "2025年=100",
                                "$": "111.2",
                            },
                            {
                                "@tab": "1",
                                "@cat01": "0002",
                                "@area": "00000",
                                "@time": "2026070000",
                                "@unit": "2025年=100",
                                "@annotation": "A1",
                                "$": "109.8",
                            },
                            {
                                "@tab": "1",
                                "@cat01": "9999",
                                "@area": "00000",
                                "@time": "2026070000",
                                "$": "-",
                            },
                        ],
                    },
                },
            }
        },
    }


def test_estat_search_metadata_and_data_preserve_official_dimensions_and_cache() -> None:
    client = EstatClient(Settings(mode="personal", estat_app_id="registered-app"))
    fake = FakeHttpClient(fixture_payloads())
    client.client = fake  # type: ignore[assignment]

    search = client.search_tables("消費者物価指数")
    assert search.matched_count == 1
    assert search.tables[0].stats_data_id == "0003427113"
    assert search.tables[0].gov_org == "総務省"
    assert search.provenance.provider == "estat"
    assert search.provenance.license_class == LicenseClass.OFFICIAL_PUBLIC

    metadata = client.metadata("0003427113")
    assert [item.id for item in metadata.dimensions] == ["tab", "cat01", "area", "time"]
    assert metadata.dimensions[1].items[1].name == "生鮮食品を除く総合"

    data = client.data(
        "0003427113",
        filters={"cd_tab": "1", "cd_cat01": "0001,0002", "cd_area": "00000"},
    )
    assert data.total_number == 3
    assert data.values[0].numeric_value == Decimal("111.2")
    assert data.values[0].dimensions == {
        "tab": "1",
        "cat01": "0001",
        "area": "00000",
        "time": "2026070000",
    }
    assert data.values[1].annotation == "A1"
    assert data.values[2].numeric_value is None
    assert data.notes["-"] == "該当数値なし"
    assert data.annotations["A1"] == "fixture annotation"

    client.search_tables("消費者物価指数")
    client.metadata("0003427113")
    client.data(
        "0003427113",
        filters={"cd_tab": "1", "cd_cat01": "0001,0002", "cd_area": "00000"},
    )
    assert len(fake.calls) == 3
    assert all(call[1]["appId"] == "registered-app" for call in fake.calls)
    assert fake.calls[2][1]["cdCat01"] == "0001,0002"
    assert fake.calls[2][1]["replaceSpChar"] == 0


def test_estat_requires_app_id_and_rejects_unbounded_or_unknown_filters() -> None:
    missing = EstatClient(Settings(mode="personal"))
    with pytest.raises(RuntimeError, match="YOWAYOWA_ESTAT_APP_ID"):
        missing.search_tables("GDP")

    client = EstatClient(Settings(mode="personal", estat_app_id="registered-app"))
    with pytest.raises(ValueError, match="between 1 and 100"):
        client.search_tables("GDP", limit=101)
    with pytest.raises(ValueError, match="between 1 and 10000"):
        client.data("123", limit=10001)
    with pytest.raises(ValueError, match="Unsupported e-Stat filter"):
        client.data("123", filters={"arbitrary": "value"})


def test_estat_api_error_is_reported_without_request_url_or_app_id() -> None:
    client = EstatClient(Settings(mode="personal", estat_app_id="super-secret-app-id"))
    fake = FakeHttpClient(
        {
            "getStatsList": {
                "GET_STATS_LIST": {"RESULT": {"STATUS": 100, "ERROR_MSG": "認証に失敗しました。"}}
            }
        }
    )
    client.client = fake  # type: ignore[assignment]

    with pytest.raises(LookupError) as exc:
        client.search_tables("GDP")
    message = str(exc.value)
    assert "e-Stat API error 100" in message
    assert "super-secret-app-id" not in message
    assert "api.e-stat.go.jp" not in message
