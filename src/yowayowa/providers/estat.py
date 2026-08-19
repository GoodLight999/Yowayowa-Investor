from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from cachetools import TTLCache

from yowayowa.config import Settings
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.estat_models import (
    EstatClassItem,
    EstatData,
    EstatDimension,
    EstatMetadata,
    EstatTableSearch,
    EstatTableSummary,
    EstatValue,
)
from yowayowa.providers.base import enforce_source_policy

QueryParamValue = str | int | float | bool | None

_ALLOWED_FILTERS = {
    "cd_tab": "cdTab",
    "cd_time": "cdTime",
    "cd_area": "cdArea",
    **{f"cd_cat{index:02d}": f"cdCat{index:02d}" for index in range(1, 16)},
}


class EstatClient:
    base_url = "https://api.e-stat.go.jp/rest/3.0/app/json"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        enforce_source_policy("estat", mode=settings.mode)
        self.client = httpx.Client(timeout=settings.request_timeout_seconds)
        ttl = max(3600, settings.cache_ttl_seconds)
        self._search_cache: TTLCache[tuple[str, str, int], EstatTableSearch] = TTLCache(
            maxsize=64,
            ttl=ttl,
        )
        self._meta_cache: TTLCache[tuple[str, str], EstatMetadata] = TTLCache(
            maxsize=128,
            ttl=ttl,
        )
        self._data_cache: TTLCache[tuple[object, ...], EstatData] = TTLCache(
            maxsize=128,
            ttl=ttl,
        )

    def _app_id(self) -> str:
        if not self.settings.estat_app_id:
            raise RuntimeError("e-Stat requires YOWAYOWA_ESTAT_APP_ID")
        return self.settings.estat_app_id

    @staticmethod
    def _provenance(*, as_of: datetime | date | None = None) -> Provenance:
        now = datetime.now(UTC)
        return Provenance(
            provider="estat",
            source="Government of Japan e-Stat API",
            source_url="https://www.e-stat.go.jp/",
            license_class=LicenseClass.OFFICIAL_PUBLIC,
            retrieved_at=now,
            as_of=as_of or now,
            notes=[
                "Source: Portal Site of Official Statistics of Japan (e-Stat).",
                "Commercial reuse is permitted under the e-Stat terms; Yowayowa identifies its "
                "own processing separately from the source data.",
                "API access requires a registered e-Stat application ID.",
            ],
        )

    @staticmethod
    def _as_list(value: object) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [value]
        return []

    @staticmethod
    def _text(value: object) -> str | None:
        if isinstance(value, dict):
            raw = value.get("$")
            return str(raw).strip() if raw is not None else None
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _attr(value: object, name: str) -> str | None:
        if not isinstance(value, dict):
            return None
        raw = value.get(f"@{name}")
        if raw is None:
            return None
        text = str(raw).strip()
        return text or None

    @staticmethod
    def _integer(value: object) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(str(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _date(value: object) -> date | None:
        if value in (None, ""):
            return None
        text = str(value).strip()
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None

    @staticmethod
    def _updated(value: object) -> datetime | date | None:
        if value in (None, ""):
            return None
        text = str(value).strip()
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return EstatClient._date(text)

    @staticmethod
    def _number(value: str) -> Decimal | None:
        text = value.strip().replace(",", "")
        if not text or text in {"-", "...", "…", "X", "x", "NA", "N/A"}:
            return None
        try:
            return Decimal(text)
        except InvalidOperation:
            return None

    def _get(
        self,
        endpoint: str,
        root_name: str,
        params: dict[str, QueryParamValue],
    ) -> dict[str, Any]:
        request_params: dict[str, QueryParamValue] = {"appId": self._app_id(), **params}
        response = self.client.get(f"{self.base_url}/{endpoint}", params=request_params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise LookupError("Unexpected e-Stat response")
        root = payload.get(root_name)
        if not isinstance(root, dict):
            raise LookupError("Unexpected e-Stat response root")
        result = root.get("RESULT")
        if not isinstance(result, dict):
            raise LookupError("Missing e-Stat result metadata")
        status = self._integer(result.get("STATUS"))
        if status is None:
            raise LookupError("Invalid e-Stat result status")
        if status >= 100:
            message = self._text(result.get("ERROR_MSG")) or "e-Stat API error"
            raise LookupError(f"e-Stat API error {status}: {message}")
        return root

    @classmethod
    def _table_summary(cls, raw: dict[str, Any]) -> EstatTableSummary | None:
        stats_data_id = str(raw.get("@id") or "").strip()
        stat_name = cls._text(raw.get("STAT_NAME"))
        title = cls._text(raw.get("TITLE"))
        if not stats_data_id or not stat_name or not title:
            return None
        stat_name_node = raw.get("STAT_NAME")
        gov_org_node = raw.get("GOV_ORG")
        title_node = raw.get("TITLE")
        main_node = raw.get("MAIN_CATEGORY")
        sub_node = raw.get("SUB_CATEGORY")
        return EstatTableSummary(
            stats_data_id=stats_data_id,
            stats_code=cls._attr(stat_name_node, "code"),
            stat_name=stat_name,
            gov_org_code=cls._attr(gov_org_node, "code"),
            gov_org=cls._text(gov_org_node),
            statistics_name=cls._text(raw.get("STATISTICS_NAME")),
            title=title,
            table_no=cls._attr(title_node, "no"),
            cycle=cls._text(raw.get("CYCLE")),
            survey_date=cls._text(raw.get("SURVEY_DATE")),
            open_date=cls._date(raw.get("OPEN_DATE")),
            collect_area=cls._text(raw.get("COLLECT_AREA")),
            main_category_code=cls._attr(main_node, "code"),
            main_category=cls._text(main_node),
            sub_category_code=cls._attr(sub_node, "code"),
            sub_category=cls._text(sub_node),
            total_number=cls._integer(raw.get("OVERALL_TOTAL_NUMBER")),
            updated_at=cls._updated(raw.get("UPDATED_DATE")),
        )

    @classmethod
    def _dimensions(cls, class_inf: object) -> list[EstatDimension]:
        if not isinstance(class_inf, dict):
            return []
        dimensions: list[EstatDimension] = []
        for raw_dimension in cls._as_list(class_inf.get("CLASS_OBJ")):
            dimension_id = str(raw_dimension.get("@id") or "").strip()
            name = str(raw_dimension.get("@name") or "").strip()
            if not dimension_id or not name:
                continue
            items: list[EstatClassItem] = []
            for raw_item in cls._as_list(raw_dimension.get("CLASS")):
                code = str(raw_item.get("@code") or "").strip()
                item_name = str(raw_item.get("@name") or "").strip()
                if not code or not item_name:
                    continue
                items.append(
                    EstatClassItem(
                        code=code,
                        name=item_name,
                        level=cls._integer(raw_item.get("@level")),
                        parent_code=(
                            str(raw_item.get("@parentCode")).strip()
                            if raw_item.get("@parentCode") is not None
                            else None
                        ),
                        unit=(
                            str(raw_item.get("@unit")).strip()
                            if raw_item.get("@unit") is not None
                            else None
                        ),
                    )
                )
            dimensions.append(EstatDimension(id=dimension_id, name=name, items=items))
        return dimensions

    def search_tables(self, query: str, *, lang: str = "J", limit: int = 50) -> EstatTableSearch:
        normalized_query = query.strip()
        normalized_lang = lang.strip().upper()
        if not normalized_query:
            raise ValueError("e-Stat search query is required")
        if normalized_lang not in {"J", "E"}:
            raise ValueError("e-Stat lang must be J or E")
        if limit < 1 or limit > 100:
            raise ValueError("e-Stat search limit must be between 1 and 100")
        cache_key = (normalized_query, normalized_lang, limit)
        cached = self._search_cache.get(cache_key)
        if cached is not None:
            return cached

        root = self._get(
            "getStatsList",
            "GET_STATS_LIST",
            {
                "lang": normalized_lang,
                "searchWord": normalized_query,
                "searchKind": 1,
                "explanationGetFlg": "N",
                "limit": limit,
            },
        )
        data = root.get("DATALIST_INF")
        if not isinstance(data, dict):
            result = EstatTableSearch(
                query=normalized_query,
                tables=[],
                matched_count=0,
                provenance=self._provenance(),
            )
            self._search_cache[cache_key] = result
            return result
        tables = [
            summary
            for raw in self._as_list(data.get("TABLE_INF"))
            if (summary := self._table_summary(raw)) is not None
        ]
        result_inf = data.get("RESULT_INF")
        next_key = (
            self._integer(result_inf.get("NEXT_KEY")) if isinstance(result_inf, dict) else None
        )
        result = EstatTableSearch(
            query=normalized_query,
            tables=tables,
            matched_count=self._integer(data.get("NUMBER")) or len(tables),
            next_key=next_key,
            provenance=self._provenance(),
        )
        self._search_cache[cache_key] = result
        return result

    def metadata(self, stats_data_id: str, *, lang: str = "J") -> EstatMetadata:
        normalized_id = stats_data_id.strip()
        normalized_lang = lang.strip().upper()
        if not normalized_id or len(normalized_id) > 64:
            raise ValueError("Invalid e-Stat statistics table ID")
        if normalized_lang not in {"J", "E"}:
            raise ValueError("e-Stat lang must be J or E")
        cache_key = (normalized_id, normalized_lang)
        cached = self._meta_cache.get(cache_key)
        if cached is not None:
            return cached

        root = self._get(
            "getMetaInfo",
            "GET_META_INFO",
            {
                "lang": normalized_lang,
                "statsDataId": normalized_id,
                "explanationGetFlg": "N",
            },
        )
        metadata_inf = root.get("METADATA_INF")
        if not isinstance(metadata_inf, dict):
            raise LookupError(f"No e-Stat metadata returned for {normalized_id}")
        raw_table = metadata_inf.get("TABLE_INF")
        table = self._table_summary(raw_table) if isinstance(raw_table, dict) else None
        result = EstatMetadata(
            stats_data_id=normalized_id,
            table=table,
            dimensions=self._dimensions(metadata_inf.get("CLASS_INF")),
            provenance=self._provenance(as_of=table.updated_at if table else None),
        )
        self._meta_cache[cache_key] = result
        return result

    def data(
        self,
        stats_data_id: str,
        *,
        lang: str = "J",
        filters: dict[str, str] | None = None,
        limit: int = 5000,
        start_position: int | None = None,
    ) -> EstatData:
        normalized_id = stats_data_id.strip()
        normalized_lang = lang.strip().upper()
        if not normalized_id or len(normalized_id) > 64:
            raise ValueError("Invalid e-Stat statistics table ID")
        if normalized_lang not in {"J", "E"}:
            raise ValueError("e-Stat lang must be J or E")
        if limit < 1 or limit > 10_000:
            raise ValueError("e-Stat data limit must be between 1 and 10000")
        if start_position is not None and start_position < 1:
            raise ValueError("e-Stat start_position must be positive")

        normalized_filters: dict[str, str] = {}
        for key, filter_value in (filters or {}).items():
            if key not in _ALLOWED_FILTERS:
                raise ValueError(f"Unsupported e-Stat filter: {key}")
            cleaned = filter_value.strip()
            if not cleaned or len(cleaned) > 512:
                raise ValueError(f"Invalid e-Stat filter value for {key}")
            normalized_filters[key] = cleaned
        cache_key: tuple[object, ...] = (
            normalized_id,
            normalized_lang,
            limit,
            start_position,
            *sorted(normalized_filters.items()),
        )
        cached = self._data_cache.get(cache_key)
        if cached is not None:
            return cached

        params: dict[str, QueryParamValue] = {
            "lang": normalized_lang,
            "statsDataId": normalized_id,
            "metaGetFlg": "Y",
            "explanationGetFlg": "N",
            "annotationGetFlg": "Y",
            "replaceSpChar": 0,
            "limit": limit,
        }
        if start_position is not None:
            params["startPosition"] = start_position
        for key, filter_value in normalized_filters.items():
            params[_ALLOWED_FILTERS[key]] = filter_value

        root = self._get("getStatsData", "GET_STATS_DATA", params)
        statistical_data = root.get("STATISTICAL_DATA")
        if not isinstance(statistical_data, dict):
            raise LookupError(f"No e-Stat data returned for {normalized_id}")
        result_inf = statistical_data.get("RESULT_INF")
        if not isinstance(result_inf, dict):
            raise LookupError("Missing e-Stat data result metadata")
        raw_table = statistical_data.get("TABLE_INF")
        table = self._table_summary(raw_table) if isinstance(raw_table, dict) else None
        dimensions = self._dimensions(statistical_data.get("CLASS_INF"))
        data_inf = statistical_data.get("DATA_INF")
        if not isinstance(data_inf, dict):
            data_inf = {}

        notes = {
            str(item.get("@char")): str(item.get("$") or "")
            for item in self._as_list(data_inf.get("NOTE"))
            if item.get("@char") is not None
        }
        annotations = {
            str(item.get("@annotation")): str(item.get("$") or "")
            for item in self._as_list(data_inf.get("ANNOTATION"))
            if item.get("@annotation") is not None
        }
        values: list[EstatValue] = []
        for raw in self._as_list(data_inf.get("VALUE")):
            source_value = self._text(raw.get("$"))
            if source_value is None:
                continue
            dimension_values = {
                key.removeprefix("@"): str(raw_value)
                for key, raw_value in raw.items()
                if key.startswith("@") and key not in {"@unit", "@annotation"}
            }
            values.append(
                EstatValue(
                    value=source_value,
                    numeric_value=self._number(source_value),
                    unit=self._attr(raw, "unit"),
                    annotation=self._attr(raw, "annotation"),
                    dimensions=dimension_values,
                )
            )

        result = EstatData(
            stats_data_id=normalized_id,
            total_number=self._integer(result_inf.get("TOTAL_NUMBER")) or len(values),
            from_number=self._integer(result_inf.get("FROM_NUMBER")),
            to_number=self._integer(result_inf.get("TO_NUMBER")),
            next_key=self._integer(result_inf.get("NEXT_KEY")),
            table=table,
            dimensions=dimensions,
            values=values,
            notes=notes,
            annotations=annotations,
            provenance=self._provenance(as_of=table.updated_at if table else None),
        )
        self._data_cache[cache_key] = result
        return result
