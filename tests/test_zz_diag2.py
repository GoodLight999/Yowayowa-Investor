"""Diagnostic round 2: run the EXACT failing assertion from inside a captured
(not disabled) test, so we see what result.output holds under CI capture.
"""

from __future__ import annotations

from typer.testing import CliRunner

from yowayowa.cli_entry import app


def test_diag_exact_assertion() -> None:
    """Mirrors tests/test_preset_snapshot_cli.py::test_run_builtin_help_is_available."""
    result = CliRunner().invoke(app, ["preset", "run-builtin", "--help"])
    print("DIAG2 exit:", result.exit_code)
    print("DIAG2 len:", len(result.output))
    print("DIAG2 repr-head:", repr(result.output[:200]))
    print("DIAG2 repr-tail:", repr(result.output[-200:]))
    print("DIAG2 has-flag:", "--via-api" in result.output)
    assert "--via-api" in result.output, result.output
