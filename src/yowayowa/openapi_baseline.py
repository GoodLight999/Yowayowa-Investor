"""Generated-vs-committed OpenAPI baseline gate.

``openapi.json`` is a local build artifact and stays git-ignored (``.gitignore``),
so the committed document at ``docs/openapi_baseline.json`` is the contract of
record: CI regenerates ``app.openapi()`` and fails when the result differs from
the baseline. That closes the drift window opened by the previous workflow,
which only asserted that the freshly generated file was non-empty.

Update procedure (intentional contract changes only) lives in
``docs/OPENAPI_BASELINE.md``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

BASELINE_RELATIVE_PATH = Path("docs") / "openapi_baseline.json"
UPDATE_PROCEDURE_DOC = "docs/OPENAPI_BASELINE.md"

_HTTP_METHODS = frozenset({"delete", "get", "head", "options", "patch", "post", "put", "trace"})
_MAX_VALUE_CHARS = 160


def repository_root() -> Path:
    """Return the checkout root that owns this package's ``src`` tree."""

    return Path(__file__).resolve().parents[2]


def default_baseline_path() -> Path:
    return repository_root() / BASELINE_RELATIVE_PATH


def current_spec() -> dict[str, Any]:
    """Return the OpenAPI document the current application generates."""

    from yowayowa.api.app import app

    spec: dict[str, Any] = app.openapi()
    return spec


def canonical_json(spec: Mapping[str, Any]) -> str:
    """Serialize a spec with stable key order so the baseline diffs cleanly."""

    return json.dumps(spec, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def load_baseline(path: Path | None = None) -> dict[str, Any]:
    target = path if path is not None else default_baseline_path()
    loaded: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
    return loaded


def write_baseline(path: Path | None = None, spec: Mapping[str, Any] | None = None) -> Path:
    """Rewrite the baseline from the current application (intentional changes)."""

    target = path if path is not None else default_baseline_path()
    payload = spec if spec is not None else current_spec()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(canonical_json(payload), encoding="utf-8")
    return target


def _escape_pointer(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _flatten(value: Any, pointer: str = "") -> Iterator[tuple[str, Any]]:
    """Yield ``(json_pointer, scalar)`` pairs, including empty containers."""

    if isinstance(value, Mapping) and value:
        for key in sorted(value, key=str):
            child = f"{pointer}/{_escape_pointer(str(key))}"
            yield from _flatten(value[key], child)
    elif isinstance(value, list) and value:
        for index, item in enumerate(value):
            yield from _flatten(item, f"{pointer}/{index}")
    else:
        yield pointer, value


def _format_value(value: Any) -> str:
    if isinstance(value, str):
        rendered = json.dumps(value, ensure_ascii=False)
    else:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(rendered) > _MAX_VALUE_CHARS:
        return rendered[: _MAX_VALUE_CHARS - 1] + "…"
    return rendered


def _operations(spec: Mapping[str, Any]) -> dict[str, frozenset[str]]:
    """Map each documented path to the HTTP methods the spec declares."""

    operations: dict[str, frozenset[str]] = {}
    paths = spec.get("paths")
    if not isinstance(paths, Mapping):
        return operations
    for path, path_item in paths.items():
        if not isinstance(path_item, Mapping):
            continue
        methods = frozenset(
            str(method).upper() for method in path_item if str(method).lower() in _HTTP_METHODS
        )
        if methods:
            operations[str(path)] = methods
    return operations


def count_operations(spec: Mapping[str, Any]) -> int:
    """Return the number of (path, method) operations the spec documents."""

    return sum(len(methods) for methods in _operations(spec).values())


def describe_differences(
    baseline: Mapping[str, Any],
    generated: Mapping[str, Any],
    *,
    max_details: int = 40,
) -> list[str]:
    """Return a human-readable difference report; an empty list means no drift."""

    if baseline == generated:
        return []

    lines: list[str] = []
    baseline_operations = _operations(baseline)
    generated_operations = _operations(generated)
    changed_paths: set[str] = set()

    for path in sorted(set(baseline_operations) | set(generated_operations)):
        before = baseline_operations.get(path, frozenset())
        after = generated_operations.get(path, frozenset())
        for method in sorted(after - before):
            lines.append(f"  + {method} {path}")
            changed_paths.add(path)
        for method in sorted(before - after):
            lines.append(f"  - {method} {path}")
            changed_paths.add(path)
    if lines:
        lines.insert(0, f"operations added or removed: {len(lines)}")

    baseline_leaves = dict(_flatten(baseline))
    generated_leaves = dict(_flatten(generated))
    suppressed_prefixes = tuple("/paths/" + _escape_pointer(path) for path in sorted(changed_paths))

    value_lines: list[str] = []
    for pointer in sorted(set(baseline_leaves) | set(generated_leaves)):
        if pointer.startswith(suppressed_prefixes):
            continue
        before_present = pointer in baseline_leaves
        after_present = pointer in generated_leaves
        if before_present and after_present:
            if baseline_leaves[pointer] == generated_leaves[pointer]:
                continue
            delta = (
                f"{_format_value(baseline_leaves[pointer])} -> "
                f"{_format_value(generated_leaves[pointer])}"
            )
        elif after_present:
            delta = f"(absent in baseline) -> {_format_value(generated_leaves[pointer])}"
        else:
            delta = f"{_format_value(baseline_leaves[pointer])} -> (absent in generated)"
        value_lines.append(f"  {pointer or '/'}: {delta}")

    if value_lines:
        lines.append(f"schema or value differences: {len(value_lines)} (baseline -> generated)")
        lines.extend(value_lines[:max_details])
        hidden = len(value_lines) - max_details
        if hidden > 0:
            lines.append(f"  ... {hidden} more difference(s) not shown; raise --max-details")

    return lines
