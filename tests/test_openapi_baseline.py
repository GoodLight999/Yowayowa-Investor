"""Gate pins for the committed OpenAPI baseline (docs/openapi_baseline.json).

The generated document is checked twice: by
``scripts/check_openapi_baseline.py`` in CI/``make verify`` and by the first
test below, so a plain ``pytest`` run also refuses to let the contract drift.
"""

import json
from typing import Any

from yowayowa.openapi_baseline import (
    canonical_json,
    count_operations,
    current_spec,
    default_baseline_path,
    describe_differences,
    load_baseline,
)


def _generated_copy() -> dict[str, Any]:
    spec: dict[str, Any] = json.loads(canonical_json(current_spec()))
    return spec


def test_committed_baseline_matches_generated_document() -> None:
    assert describe_differences(load_baseline(), current_spec()) == []


def test_committed_baseline_is_canonically_formatted() -> None:
    """Regenerating with --write must not produce spurious file diffs."""

    path = default_baseline_path()
    assert path.read_text(encoding="utf-8") == canonical_json(load_baseline(path))


def test_baseline_records_operations_missing_from_the_stale_artifact() -> None:
    """docs/OPENAPI_DRIFT_20261023.md §3/§6 drift that this baseline absorbed."""

    baseline = load_baseline()

    assert "delete" in baseline["paths"]["/v1/portfolios/{portfolio_id}"]
    assert "post" in baseline["paths"]["/v1/sizing/quote-idea"]
    assert count_operations(baseline) >= 155


def test_removed_operation_is_reported_without_child_noise() -> None:
    generated = _generated_copy()
    baseline = _generated_copy()
    del baseline["paths"]["/v1/ai/chat"]["post"]

    report = describe_differences(baseline, generated)

    assert report == ["operations added or removed: 1", "  + POST /v1/ai/chat"]


def test_added_operation_is_reported() -> None:
    generated = _generated_copy()
    baseline = _generated_copy()
    del generated["paths"]["/v1/sizing/quote-idea"]["post"]

    report = describe_differences(baseline, generated)

    assert report[0] == "operations added or removed: 1"
    assert "  - POST /v1/sizing/quote-idea" in report


def test_single_value_change_is_reported_with_json_pointer() -> None:
    generated = _generated_copy()
    baseline = _generated_copy()
    baseline["info"]["version"] = "0.1.0-stale"

    report = describe_differences(baseline, generated)

    assert report[0] == "schema or value differences: 1 (baseline -> generated)"
    assert report[1] == '  /info/version: "0.1.0-stale" -> "0.1.0"'


def test_enum_value_change_is_reported() -> None:
    generated = _generated_copy()
    baseline = _generated_copy()
    baseline["components"]["schemas"]["LicenseClass"]["enum"].remove("synthetic")

    report = describe_differences(baseline, generated)

    assert any("LicenseClass/enum" in line and "synthetic" in line for line in report)


def test_identical_documents_produce_no_report() -> None:
    generated = _generated_copy()

    assert describe_differences(generated, _generated_copy()) == []


def test_detail_output_is_capped() -> None:
    generated = _generated_copy()
    baseline = _generated_copy()
    baseline["info"]["version"] = "0.1.0-stale"
    baseline["info"]["title"] = "stale title"

    report = describe_differences(baseline, generated, max_details=1)

    assert report[0] == "schema or value differences: 2 (baseline -> generated)"
    assert len(report) == 3
    assert "more difference(s) not shown" in report[-1]
