"""Rejected operational scripts must not silently resume live execution."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("args", [[], ["--dry-run"]])
def test_retired_forward_entrypoint_refuses_before_any_io(tmp_path: Path, args: list[str]) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/hl_testnet_s07/s07_daily_forward.py"), *args],
        cwd=tmp_path,
        env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "DISABLED: t_f840d5bc" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_rejected_sources_are_byte_preserved_but_not_executable() -> None:
    import hashlib

    archive = ROOT / "docs/evidence/hl_exec_disposition_20261108/rejected"
    rows = (archive / "SHA256SUMS.original").read_text().splitlines()
    assert len(rows) == 10
    for row in rows:
        digest, old_path = row.split(maxsplit=1)
        candidate = archive / (Path(old_path).name + ".txt")
        assert hashlib.sha256(candidate.read_bytes()).hexdigest() == digest
        assert candidate.stat().st_mode & 0o111 == 0
        if old_path != "scripts/hl_testnet_s07/s07_daily_forward.py":
            assert not (ROOT / old_path).exists()
