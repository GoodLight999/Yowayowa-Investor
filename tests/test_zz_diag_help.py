"""Temporary diagnostic (CTO): print the exact --help output CI renders for the
two P3-A commands, ANSI-stripped, so the real cause is read from CI, not guessed.
"""

from __future__ import annotations

import re
import sys

from typer.testing import CliRunner

from yowayowa.cli_entry import app

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def test_diag_help_render(capsys) -> None:
    runner = CliRunner()
    with capsys.disabled():
        print("### DIAG python:", sys.version)
        try:
            import click
            import typer

            print("### DIAG typer", typer.__version__, "click", click.__version__)
        except Exception as exc:
            print("### DIAG version err", exc)
        import os

        print("### DIAG COLUMNS=", os.environ.get("COLUMNS"), "TERM=", os.environ.get("TERM"))
        for args in (
            ["preset", "run-builtin", "--help"],
            ["preset", "snapshot-builtins", "--help"],
        ):
            r = runner.invoke(app, args)
            plain = ANSI.sub("", r.output)
            print(f"### DIAG {args} exit={r.exit_code} via-api={'--via-api' in plain}")
            for line in plain.splitlines():
                if line.strip():
                    print("### DIAG |", line.rstrip()[:110])
