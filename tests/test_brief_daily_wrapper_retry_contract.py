"""Job-level retry contract of the brief-daily cron wrapper (t_ee4056a0 Round 2).

Retry ownership is single-layered: the bounded transport backoff in
``yowayowa.services.ai_agent._post_json`` is the only retry owner
(transient 429 shapes: at most 3 attempts with 30s/90s waits; window and
plan codes such as 1308/1311/1313: a single attempt, no resend). The cron
wrapper ``/root/.hermes/scripts/yowayowa_brief_daily.sh`` must NOT retry on
top of it — the old wrapper re-ran every nonzero exit up to 4 times
(60/180/540s), multiplying the attempt count and defeating the fail-fast
codes.

The tests execute a sanitized copy of the real wrapper: the ``.env`` source
line is removed, the working directory is isolated, and the CLI invocation is
replaced with a stub runner that calls the real ``_post_json`` with a stubbed
``httpx.post`` and ``time.sleep``. There are NO network requests, NO actual
sleeps and NO quota consumption; the only credential in play is a dummy value
that must never appear in the wrapper output.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

WRAPPER = Path("/root/.hermes/scripts/yowayowa_brief_daily.sh")
REPO_ROOT = Path(__file__).resolve().parents[1]


def _wrapper_deployed() -> bool:
    # Path.exists() raises PermissionError on hosts where /root is not
    # traversable (e.g. GitHub Actions runners), which aborts collection
    # before the skip below can apply. Treat that as "not deployed".
    try:
        return WRAPPER.exists()
    except OSError:
        return False


pytestmark = [
    pytest.mark.skipif(
        not _wrapper_deployed(),
        reason="brief-daily wrapper is not deployed on this host",
    ),
]

_RUNNER = """
import json
import os
from pathlib import Path
from unittest.mock import Mock
import httpx
from yowayowa.config import Settings
from yowayowa.services import ai_agent

log = Path(os.environ["REVIEW_EVENTS"])
def record(kind, value):
    with log.open("a") as fh:
        fh.write(json.dumps({"kind": kind, "value": value}) + "\\n")

scenario = os.environ["REVIEW_SCENARIO"]
def post(url, **kwargs):
    record("post", scenario)
    request = httpx.Request("POST", "https://provider.example/v1/chat/completions")
    if scenario == "success":
        return httpx.Response(200, json={"ok": True}, request=request)
    if scenario == "500":
        return httpx.Response(500, json={"error": {"message": "fixture error"}}, request=request)
    return httpx.Response(
        429, json={"error": {"code": scenario, "message": "fixture only"}}, request=request
    )

ai_agent.httpx.post = post
ai_agent.time.sleep = lambda delay: record("inner_sleep", delay)
agent = ai_agent.InvestmentResearchAgent(
    Settings(_env_file=None, database_url="sqlite:///:memory:"), Mock()
)
agent._post_json(
    "https://provider.example/v1/chat/completions",
    headers={"Authorization": "Bearer " + os.environ["ZAI_API_KEY"]},
    body={},
)
"""


def _sanitized_copy(tmp_path: Path, runner: Path) -> str:
    source = WRAPPER.read_text()
    source, count = re.subn(
        r"^set -a; source /root/\.hermes/\.env; set \+a$",
        ": # isolated: no secret file is loaded",
        source,
        flags=re.M,
    )
    assert count == 1, "refuse to execute a fixture with an unsanitized environment source"
    source, count = re.subn(
        r"^cd /root/current-work/yowayowa-investor \|\| exit 1$",
        f"cd {tmp_path} || exit 1",
        source,
        flags=re.M,
    )
    assert count == 1
    source, count = re.subn(
        r"^  \.venv/bin/python -c \"\n.*?^\"\n",
        f"  {sys.executable} {runner}\n",
        source,
        flags=re.M | re.S,
    )
    assert count == 1, "refuse to execute a fixture with a real CLI command"
    assert "source /root/.hermes/.env" not in source
    return source


def _run_wrapper(
    tmp_path: Path, scenario: str
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]], list[str]]:
    runner = tmp_path / "stub_runner.py"
    runner.write_text(_RUNNER)
    script = tmp_path / "sanitized-wrapper.sh"
    script.write_text(_sanitized_copy(tmp_path, runner))
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir(exist_ok=True)
    sleep = stub_bin / "sleep"
    sleep.write_text('#!/bin/sh\nprintf \'%s\\n\' "$1" >> "$REVIEW_SLEEP_LOG"\n')
    sleep.chmod(0o700)
    events = tmp_path / "events.jsonl"
    sleep_log = tmp_path / "outer_sleeps.txt"
    env = {
        "PATH": f"{stub_bin}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "TMPDIR": str(tmp_path),
        "ZAI_API_KEY": "dummy-wrapper-review-value",
        "REVIEW_SCENARIO": scenario,
        "REVIEW_EVENTS": str(events),
        "REVIEW_SLEEP_LOG": str(sleep_log),
    }
    result = subprocess.run(
        ["/bin/bash", str(script)], env=env, capture_output=True, text=True, timeout=120
    )
    records: list[dict[str, object]] = []
    if events.exists():
        records = [json.loads(line) for line in events.read_text().splitlines()]
    outer = sleep_log.read_text().splitlines() if sleep_log.exists() else []
    combined = result.stdout + result.stderr
    assert "dummy-wrapper-review-value" not in combined, "wrapper must not echo credentials"
    return result, records, outer


def test_wrapper_has_no_own_retry_loop() -> None:
    """The wrapper itself never retries: the transport backoff is the single owner."""
    text = WRAPPER.read_text()
    assert "while " not in text, "wrapper must not loop over attempts"
    assert "sleep" not in text, "wrapper must not sleep between retries"
    assert "BRIEF_OK attempt=1" in text
    assert "BRIEF_FAILED" in text


@pytest.mark.parametrize(
    ("scenario", "expected_calls"),
    [("success", 1), ("1302", 3), ("1308", 1), ("1313", 1), ("500", 1)],
)
def test_job_level_retry_contract(tmp_path: Path, scenario: str, expected_calls: int) -> None:
    result, records, outer_sleeps = _run_wrapper(tmp_path, scenario)
    calls = [r for r in records if r["kind"] == "post"]
    inner_sleeps = [r["value"] for r in records if r["kind"] == "inner_sleep"]
    assert len(calls) == expected_calls, (scenario, len(calls))
    assert outer_sleeps == [], (scenario, outer_sleeps)
    if scenario == "success":
        assert result.returncode == 0
        assert "BRIEF_OK attempt=1" in result.stdout
        assert inner_sleeps == []
    else:
        assert result.returncode == 1, (scenario, result.returncode)
        assert "BRIEF_FAILED" in result.stdout
        if scenario == "1302":
            assert inner_sleeps == [30, 90]
            assert "AI provider rate limit (HTTP 429)" in result.stderr
            assert "[code=1302]" in result.stderr
        else:
            # window/plan codes and non-429 failures stop after a single attempt
            assert inner_sleeps == [], (scenario, inner_sleeps)
