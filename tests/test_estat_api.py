import pytest

from yowayowa.api.macro_routes import _estat_filters


def test_estat_filters_normalize_known_dimension_aliases() -> None:
    assert _estat_filters(["area=00000", "cat01=0001,0002", "time=2026070000", "cd_tab=1"]) == {
        "cd_area": "00000",
        "cd_cat01": "0001,0002",
        "cd_time": "2026070000",
        "cd_tab": "1",
    }


def test_estat_filters_reject_malformed_or_duplicate_dimensions() -> None:
    with pytest.raises(ValueError, match="must look like"):
        _estat_filters(["area"])
    with pytest.raises(ValueError, match="Duplicate"):
        _estat_filters(["area=00000", "cd_area=13000"])
