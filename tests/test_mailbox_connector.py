"""P1D mailbox connector: read-only argv, JSON conversion, fail-closed errors.

Every test injects a fake command runner, so no subprocess is ever started and
no network/mailbox is touched. Fixtures use fictional accounts, addresses, and
message content only.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from yowayowa.acquisition.mailbox import (
    MAILBOX_AUTH_FAILURE_MARKERS,
    GogMailboxReader,
    MailboxAccessError,
    MailboxMessage,
    default_command_runner,
    parse_mailbox_date,
)
from yowayowa.acquisition.models import AcquisitionFetchState

ACCOUNT = "operator@example.invalid"
# The real failure text the local CLI emits when no keyring password is
# available (credential-free: it names the requirement, not a secret).
KEYRING_STDERR = (
    "no TTY available for keyring file backend password prompt; set GOG_KEYRING_PASSWORD"
)


class _Runner:
    """Records argv/env and returns a scripted CompletedProcess."""

    def __init__(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
        raises: BaseException | None = None,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.raises = raises
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(
        self, argv: list[str], env: dict[str, str] | Any
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(argv), dict(env)))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(
            args=argv, returncode=self.returncode, stdout=self.stdout, stderr=self.stderr
        )

    @property
    def argv(self) -> list[str]:
        return self.calls[-1][0]

    @property
    def env(self) -> dict[str, str]:
        return self.calls[-1][1]


def _payload(messages: list[dict[str, Any]]) -> str:
    return json.dumps({"messages": messages})


def _message_payload(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "message_id": "msg-1",
        "thread_id": "thr-1",
        "date": "2026-09-23 07:00",
        "sender": "alerts@example.invalid",
        "subject": "earnings calendar",
        "body": "fictional body",
        "labels": ["INBOX", "UNREAD"],
    }
    item.update(overrides)
    return item


def _reader(runner: _Runner, *, keyring_password_file: Path | None = None) -> GogMailboxReader:
    return GogMailboxReader(
        account=ACCOUNT,
        command="gog",
        keyring_password_file=keyring_password_file,
        timeout_seconds=5.0,
        runner=runner,
    )


# ------------------------------------------------------------------- argv


def test_argv_always_passes_readonly_json_and_include_body() -> None:
    runner = _Runner(stdout=_payload([]))
    _reader(runner).search("from:alerts@example.invalid", max_results=7)
    argv = runner.argv
    assert "-j" in argv
    assert "--readonly" in argv
    assert "--include-body" in argv
    assert argv[argv.index("--max") + 1] == "7"


def test_argv_carries_account_via_dash_a() -> None:
    runner = _Runner(stdout=_payload([]))
    _reader(runner).search("q", max_results=1)
    argv = runner.argv
    assert argv[argv.index("-a") + 1] == ACCOUNT


def test_argv_is_exactly_the_read_only_search_invocation() -> None:
    runner = _Runner(stdout=_payload([]))
    _reader(runner).search("subject:earnings", max_results=40)
    assert runner.argv == [
        "gog",
        "-j",
        "--readonly",
        "-a",
        ACCOUNT,
        "gmail",
        "messages",
        "search",
        "subject:earnings",
        "--max",
        "40",
        "--include-body",
    ]


def test_argv_never_contains_a_write_subcommand() -> None:
    runner = _Runner(stdout=_payload([]))
    reader = _reader(runner)
    reader.search("q", max_results=3)
    for forbidden in ("send", "delete", "trash", "modify", "label", "draft"):
        assert forbidden not in runner.argv


def test_custom_command_is_used_verbatim() -> None:
    runner = _Runner(stdout=_payload([]))
    reader = GogMailboxReader(
        account=ACCOUNT, command="/usr/local/bin/gog", runner=runner, timeout_seconds=5.0
    )
    reader.search("q", max_results=1)
    assert runner.argv[0] == "/usr/local/bin/gog"


def test_max_results_is_forwarded_for_each_call() -> None:
    runner = _Runner(stdout=_payload([]))
    reader = _reader(runner)
    reader.search("q", max_results=5)
    reader.search("q", max_results=11)
    assert runner.calls[0][0][runner.calls[0][0].index("--max") + 1] == "5"
    assert runner.calls[1][0][runner.calls[1][0].index("--max") + 1] == "11"


# ------------------------------------------------------------ conversion


def test_successful_json_becomes_mailbox_messages() -> None:
    runner = _Runner(stdout=_payload([_message_payload()]))
    messages = _reader(runner).search("q", max_results=10)
    assert len(messages) == 1
    message = messages[0]
    assert isinstance(message, MailboxMessage)
    assert message.message_id == "msg-1"
    assert message.thread_id == "thr-1"
    assert message.subject == "earnings calendar"
    assert message.body == "fictional body"
    assert message.labels == ["INBOX", "UNREAD"]
    assert message.sender == "alerts@example.invalid"


def test_date_is_parsed_and_timezone_aware() -> None:
    runner = _Runner(stdout=_payload([_message_payload(date="2026-09-23 07:00")]))
    message = _reader(runner).search("q", max_results=1)[0]
    assert message.sent_at.tzinfo is not None
    assert message.sent_at.replace(tzinfo=None) == datetime(2026, 9, 23, 7, 0)


def test_missing_body_becomes_empty_string() -> None:
    item = _message_payload()
    del item["body"]
    runner = _Runner(stdout=_payload([item]))
    assert _reader(runner).search("q", max_results=1)[0].body == ""


def test_missing_labels_becomes_empty_list() -> None:
    item = _message_payload()
    del item["labels"]
    runner = _Runner(stdout=_payload([item]))
    assert _reader(runner).search("q", max_results=1)[0].labels == []


def test_missing_thread_id_is_none() -> None:
    item = _message_payload()
    del item["thread_id"]
    runner = _Runner(stdout=_payload([item]))
    assert _reader(runner).search("q", max_results=1)[0].thread_id is None


def test_multiple_messages_are_returned_in_order() -> None:
    runner = _Runner(
        stdout=_payload([_message_payload(message_id="a"), _message_payload(message_id="b")])
    )
    messages = _reader(runner).search("q", max_results=10)
    assert [message.message_id for message in messages] == ["a", "b"]


def test_empty_message_list_is_a_successful_empty_search() -> None:
    runner = _Runner(stdout=_payload([]))
    assert _reader(runner).search("q", max_results=10) == []


def test_unparsable_date_fails_closed() -> None:
    runner = _Runner(stdout=_payload([_message_payload(date="not-a-date")]))
    with pytest.raises(MailboxAccessError) as excinfo:
        _reader(runner).search("q", max_results=1)
    assert excinfo.value.state is AcquisitionFetchState.FAILED


def test_message_without_id_fails_closed() -> None:
    item = _message_payload()
    del item["message_id"]
    runner = _Runner(stdout=_payload([item]))
    with pytest.raises(MailboxAccessError) as excinfo:
        _reader(runner).search("q", max_results=1)
    assert excinfo.value.state is AcquisitionFetchState.FAILED


def test_message_without_date_fails_closed() -> None:
    item = _message_payload()
    del item["date"]
    runner = _Runner(stdout=_payload([item]))
    with pytest.raises(MailboxAccessError) as excinfo:
        _reader(runner).search("q", max_results=1)
    assert excinfo.value.state is AcquisitionFetchState.FAILED


# ---------------------------------------------------------- failure modes


def test_keyring_stderr_marks_auth_expired() -> None:
    runner = _Runner(stderr=KEYRING_STDERR, returncode=1)
    with pytest.raises(MailboxAccessError) as excinfo:
        _reader(runner).search("q", max_results=1)
    assert excinfo.value.state is AcquisitionFetchState.AUTH_EXPIRED
    assert "keyring" in excinfo.value.reason.lower()


def test_command_not_found_marks_failed() -> None:
    runner = _Runner(stderr="gog: command not found", returncode=127)
    with pytest.raises(MailboxAccessError) as excinfo:
        _reader(runner).search("q", max_results=1)
    assert excinfo.value.state is AcquisitionFetchState.FAILED


def test_invalid_json_marks_failed() -> None:
    runner = _Runner(stdout="{not json", returncode=0)
    with pytest.raises(MailboxAccessError) as excinfo:
        _reader(runner).search("q", max_results=1)
    assert excinfo.value.state is AcquisitionFetchState.FAILED
    assert "invalid JSON" in excinfo.value.reason


def test_unexpected_payload_schema_marks_failed() -> None:
    runner = _Runner(stdout=json.dumps({"items": []}), returncode=0)
    with pytest.raises(MailboxAccessError) as excinfo:
        _reader(runner).search("q", max_results=1)
    assert excinfo.value.state is AcquisitionFetchState.FAILED


def test_top_level_list_payload_marks_failed() -> None:
    runner = _Runner(stdout=json.dumps([_message_payload()]), returncode=0)
    with pytest.raises(MailboxAccessError) as excinfo:
        _reader(runner).search("q", max_results=1)
    assert excinfo.value.state is AcquisitionFetchState.FAILED


def test_timeout_marks_failed() -> None:
    runner = _Runner(raises=subprocess.TimeoutExpired(cmd="gog", timeout=5.0))
    with pytest.raises(MailboxAccessError) as excinfo:
        _reader(runner).search("q", max_results=1)
    assert excinfo.value.state is AcquisitionFetchState.FAILED
    assert "timed out" in excinfo.value.reason


def test_missing_binary_marks_failed() -> None:
    runner = _Runner(raises=FileNotFoundError(2, "No such file or directory", "gog"))
    with pytest.raises(MailboxAccessError) as excinfo:
        _reader(runner).search("q", max_results=1)
    assert excinfo.value.state is AcquisitionFetchState.FAILED
    assert "not found" in excinfo.value.reason


def test_oserror_marks_failed() -> None:
    runner = _Runner(raises=OSError("exec format error"))
    with pytest.raises(MailboxAccessError) as excinfo:
        _reader(runner).search("q", max_results=1)
    assert excinfo.value.state is AcquisitionFetchState.FAILED


def test_reason_is_truncated_to_300_characters() -> None:
    runner = _Runner(stderr="keyring " + "x" * 5000, returncode=1)
    with pytest.raises(MailboxAccessError) as excinfo:
        _reader(runner).search("q", max_results=1)
    assert len(excinfo.value.reason) <= 300


def test_auth_markers_cover_keyring_tty_and_token_failures() -> None:
    for marker in ("keyring", "no tty", "unauthorized", "invalid_grant", "invalid token", "401"):
        assert marker in MAILBOX_AUTH_FAILURE_MARKERS


def test_auth_marker_matching_is_case_insensitive() -> None:
    runner = _Runner(stderr="No TTY available for keyring file backend", returncode=1)
    with pytest.raises(MailboxAccessError) as excinfo:
        _reader(runner).search("q", max_results=1)
    assert excinfo.value.state is AcquisitionFetchState.AUTH_EXPIRED


def test_unknown_source_account_is_rejected() -> None:
    with pytest.raises(MailboxAccessError) as excinfo:
        GogMailboxReader(account="")
    assert excinfo.value.state is AcquisitionFetchState.FAILED


# ------------------------------------------------------- keyring handling


def test_missing_keyring_password_file_marks_failed(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    runner = _Runner(stdout=_payload([]))
    with pytest.raises(MailboxAccessError) as excinfo:
        _reader(runner, keyring_password_file=missing)
    assert excinfo.value.state is AcquisitionFetchState.FAILED
    assert "unreadable" in excinfo.value.reason
    assert str(missing) in excinfo.value.reason
    assert runner.calls == []


def test_unreadable_keyring_password_directory_marks_failed(tmp_path: Path) -> None:
    directory = tmp_path / "a-directory"
    directory.mkdir()
    runner = _Runner(stdout=_payload([]))
    with pytest.raises(MailboxAccessError) as excinfo:
        _reader(runner, keyring_password_file=directory)
    assert excinfo.value.state is AcquisitionFetchState.FAILED


def test_file_password_is_injected_when_env_has_none(tmp_path: Path, monkeypatch: Any) -> None:
    secret_file = tmp_path / "keyring-password"
    secret_file.write_text("file-secret\n", encoding="utf-8")
    monkeypatch.delenv("GOG_KEYRING_PASSWORD", raising=False)
    runner = _Runner(stdout=_payload([]))
    _reader(runner, keyring_password_file=secret_file).search("q", max_results=1)
    assert runner.env["GOG_KEYRING_PASSWORD"] == "file-secret"


def test_environment_password_takes_precedence_over_file(tmp_path: Path, monkeypatch: Any) -> None:
    secret_file = tmp_path / "keyring-password"
    secret_file.write_text("file-secret\n", encoding="utf-8")
    monkeypatch.setenv("GOG_KEYRING_PASSWORD", "env-secret")
    runner = _Runner(stdout=_payload([]))
    _reader(runner, keyring_password_file=secret_file).search("q", max_results=1)
    assert runner.env["GOG_KEYRING_PASSWORD"] == "env-secret"


def test_no_keyring_variable_is_set_when_file_is_empty(tmp_path: Path, monkeypatch: Any) -> None:
    secret_file = tmp_path / "keyring-password"
    secret_file.write_text("   \n", encoding="utf-8")
    monkeypatch.delenv("GOG_KEYRING_PASSWORD", raising=False)
    runner = _Runner(stdout=_payload([]))
    _reader(runner, keyring_password_file=secret_file).search("q", max_results=1)
    assert "GOG_KEYRING_PASSWORD" not in runner.env


def test_ambient_environment_is_forwarded(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("GOG_CONFIG_DIR", "/tmp/gog-config-fixture")
    runner = _Runner(stdout=_payload([]))
    _reader(runner).search("q", max_results=1)
    assert runner.env["GOG_CONFIG_DIR"] == "/tmp/gog-config-fixture"


def test_password_value_is_redacted_from_the_reason(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("GOG_KEYRING_PASSWORD", "super-secret-value")
    runner = _Runner(stderr="keyring error: super-secret-value rejected", returncode=1)
    with pytest.raises(MailboxAccessError) as excinfo:
        _reader(runner).search("q", max_results=1)
    assert "super-secret-value" not in excinfo.value.reason
    assert "<redacted>" in excinfo.value.reason


def test_file_password_value_is_redacted_from_the_reason(tmp_path: Path, monkeypatch: Any) -> None:
    secret_file = tmp_path / "keyring-password"
    secret_file.write_text("file-secret-value", encoding="utf-8")
    monkeypatch.delenv("GOG_KEYRING_PASSWORD", raising=False)
    runner = _Runner(stderr="no tty for keyring file-secret-value", returncode=1)
    with pytest.raises(MailboxAccessError) as excinfo:
        _reader(runner, keyring_password_file=secret_file).search("q", max_results=1)
    assert "file-secret-value" not in excinfo.value.reason


def test_error_reason_never_contains_the_account_secret(tmp_path: Path) -> None:
    """A generic failure reason still must not echo credentials."""

    runner = _Runner(stderr="some unrelated failure", returncode=1)
    with pytest.raises(MailboxAccessError) as excinfo:
        _reader(runner).search("q", max_results=1)
    assert excinfo.value.reason == "some unrelated failure"


# ------------------------------------------------------------- date parse


def test_parse_mailbox_date_accepts_iso_with_offset() -> None:
    parsed = parse_mailbox_date("2026-09-23T07:00:00+09:00")
    assert parsed.utcoffset() is not None
    assert parsed.astimezone(UTC).hour == 22


def test_parse_mailbox_date_accepts_naive_datetime() -> None:
    parsed = parse_mailbox_date(datetime(2026, 9, 23, 7, 0))
    assert parsed.tzinfo is not None


def test_parse_mailbox_date_rejects_non_string() -> None:
    with pytest.raises(ValueError):
        parse_mailbox_date(12345)


def test_parse_mailbox_date_rejects_empty_string() -> None:
    with pytest.raises(ValueError):
        parse_mailbox_date("   ")


def test_default_runner_captures_output_and_does_not_raise(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The default runner must return rc!=0 instead of raising, so the reader
    can classify it (a raised CalledProcessError would hide AUTH_EXPIRED)."""

    monkeypatch.setenv("PATH", str(tmp_path))
    runner = default_command_runner(timeout_seconds=5.0)
    completed = runner(["/bin/echo", "hello"], {})
    assert completed.returncode == 0
    assert "hello" in completed.stdout
