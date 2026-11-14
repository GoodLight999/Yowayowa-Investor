"""Read-only mailbox access over the operator's authorized mail account (P1D).

The operator (and family) own mailboxes that carry subscribed notification
mail (for example broker earnings-calendar alerts). This module reads them
through the ``gog`` CLI in a structurally read-only way:

- every invocation carries ``--readonly`` and only ``gmail messages search``
  is ever called; no write subcommand exists in this module;
- credentials never leave the local machine: the keyring password is read
  from the environment or a local file and is never logged, stored in an
  outcome, or returned to a caller (a value that leaks into an error reason
  is redacted);
- failures fail closed: an authentication/keyring problem is reported as
  ``AUTH_EXPIRED`` instead of an empty message list, and anything else
  (missing command, timeout, malformed JSON) surfaces as ``FAILED``.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError, field_validator

from yowayowa.acquisition.models import AcquisitionFetchState

# stderr markers that identify an authentication/keyring failure rather than a
# generic command failure. Matched case-insensitively against stderr.
MAILBOX_AUTH_FAILURE_MARKERS: tuple[str, ...] = (
    "keyring",
    "no tty",
    "unauthorized",
    "invalid_grant",
    "invalid token",
    "401",
)

# Maximum characters of raw stderr kept in an error reason.
_REASON_LIMIT = 300

# Values replaced in any reason text before it reaches an outcome.
_REDACTED = "<redacted>"

_SENT_AT_FORMAT = "%Y-%m-%d %H:%M"


class MailboxAccessError(RuntimeError):
    """Fail-closed mailbox access failure with an acquisition state.

    ``AUTH_EXPIRED`` means the local mail session needs operator
    reauthentication (keyring/TTY/token markers in stderr). ``FAILED`` covers
    every other cause: missing command, timeout, malformed JSON, unreadable
    keyring password file, unexpected payload schema.
    """

    def __init__(self, state: AcquisitionFetchState, reason: str) -> None:
        super().__init__(reason)
        self.state = state
        self.reason = reason


def parse_mailbox_date(raw: object) -> datetime:
    """Parse a mailbox ``date`` value into a tz-aware datetime.

    The canonical wire format is ``YYYY-MM-DD HH:MM`` in the operator's local
    time; the local timezone is attached so downstream arithmetic is never
    performed on naive timestamps. An ISO-8601 timestamp (what the CLI emits
    when it includes a zone) is also accepted. Anything else raises
    ``ValueError``: a message without a usable date is not silently dated.
    """

    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.astimezone()
    if not isinstance(raw, str):
        raise ValueError(f"unparsable mailbox date: {raw!r}")
    text = raw.strip()
    if not text:
        raise ValueError("unparsable mailbox date: empty")
    try:
        parsed = datetime.strptime(text, _SENT_AT_FORMAT)
    except ValueError:
        candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
        try:
            iso = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise ValueError(f"unparsable mailbox date: {raw!r}") from exc
        return iso if iso.tzinfo is not None else iso.astimezone()
    return parsed.astimezone()


class MailboxMessage(BaseModel):
    """One mailbox message as delivered by the read-only CLI."""

    message_id: str
    thread_id: str | None = None
    sent_at: datetime
    sender: str = ""
    subject: str = ""
    body: str = ""
    labels: list[str] = Field(default_factory=list)

    @field_validator("sent_at", mode="before")
    @classmethod
    def _sent_at_is_aware(cls, value: object) -> datetime:
        return parse_mailbox_date(value)


class MailboxReader(Protocol):
    """Minimal read-only mailbox contract required by the private sources."""

    def search(self, query: str, *, max_results: int) -> list[MailboxMessage]: ...


CommandRunner = Callable[[list[str], Mapping[str, str]], subprocess.CompletedProcess[str]]
"""Signature of the injectable mailbox command runner used by tests."""


def default_command_runner(timeout_seconds: float) -> CommandRunner:
    """Default runner: capture stdout/stderr as text, never raise on rc != 0."""

    def _run(argv: list[str], env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv,
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )

    return _run


def _redact(text: str, secrets: Sequence[str | None]) -> str:
    """Replace every occurrence of a secret value with ``<redacted>``."""

    redacted = text
    for secret in secrets:
        if secret and secret in redacted:
            redacted = redacted.replace(secret, _REDACTED)
    return redacted


class GogMailboxReader:
    """Read-only Gmail mailbox reader driven by the ``gog`` CLI.

    The only command ever executed is::

        <command> -j --readonly -a <account> gmail messages search <query> \
            --max <max_results> --include-body

    ``--readonly`` is mandatory: the reader has no write path by construction.
    """

    def __init__(
        self,
        *,
        account: str,
        command: str = "gog",
        keyring_password_file: Path | None = None,
        timeout_seconds: float = 60.0,
        runner: CommandRunner | None = None,
    ) -> None:
        if not account:
            raise MailboxAccessError(AcquisitionFetchState.FAILED, "mailbox account is required")
        self.account = account
        self.command = command
        self.keyring_password_file = keyring_password_file
        self.timeout_seconds = timeout_seconds
        self._file_secret: str | None = None
        if keyring_password_file is not None:
            self._file_secret = self._read_keyring_password(keyring_password_file)
        self._runner = runner or default_command_runner(timeout_seconds)

    # --------------------------------------------------------------- config

    @staticmethod
    def _read_keyring_password(path: Path) -> str:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise MailboxAccessError(
                AcquisitionFetchState.FAILED,
                f"keyring password file unreadable: {path}",
            ) from exc
        return raw.strip()

    def _environment(self) -> dict[str, str]:
        """Copy the process env; a file password is only a fallback.

        An existing ``GOG_KEYRING_PASSWORD`` in the environment always wins:
        the operator's explicit shell/local config must not be overridden by a
        file that happens to be present.
        """

        env = dict(os.environ)
        if not env.get("GOG_KEYRING_PASSWORD") and self._file_secret:
            env["GOG_KEYRING_PASSWORD"] = self._file_secret
        return env

    def _secrets(self) -> list[str | None]:
        return [os.environ.get("GOG_KEYRING_PASSWORD"), self._file_secret]

    def argv(self, query: str, max_results: int) -> list[str]:
        """The exact read-only argv used for one search."""

        return [
            self.command,
            "-j",
            "--readonly",
            "-a",
            self.account,
            "gmail",
            "messages",
            "search",
            query,
            "--max",
            str(max_results),
            "--include-body",
        ]

    # --------------------------------------------------------------- search

    def search(self, query: str, *, max_results: int) -> list[MailboxMessage]:
        argv = self.argv(query, max_results)
        env = self._environment()
        try:
            completed = self._runner(argv, env)
        except subprocess.TimeoutExpired as exc:
            raise MailboxAccessError(
                AcquisitionFetchState.FAILED,
                f"mailbox command timed out after {self.timeout_seconds:g}s",
            ) from exc
        except FileNotFoundError as exc:
            raise MailboxAccessError(
                AcquisitionFetchState.FAILED,
                f"mailbox command not found: {self.command}",
            ) from exc
        except OSError as exc:
            raise MailboxAccessError(
                AcquisitionFetchState.FAILED,
                f"mailbox command failed: {type(exc).__name__}",
            ) from exc

        if completed.returncode != 0:
            raise MailboxAccessError(
                self._classify(completed.stderr or ""),
                self._reason(completed.stderr or ""),
            )

        try:
            payload = json.loads(completed.stdout or "")
        except json.JSONDecodeError as exc:
            raise MailboxAccessError(
                AcquisitionFetchState.FAILED,
                f"mailbox command returned invalid JSON: {exc}",
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
            raise MailboxAccessError(
                AcquisitionFetchState.FAILED,
                "unexpected mailbox payload schema (want {'messages': [...]})",
            )
        return [self._to_message(item) for item in payload["messages"]]

    # ------------------------------------------------------------- internal

    @staticmethod
    def _classify(stderr: str) -> AcquisitionFetchState:
        lowered = stderr.lower()
        if any(marker in lowered for marker in MAILBOX_AUTH_FAILURE_MARKERS):
            return AcquisitionFetchState.AUTH_EXPIRED
        return AcquisitionFetchState.FAILED

    def _reason(self, stderr: str) -> str:
        """First 300 chars of stderr with any credential value redacted."""

        return _redact(stderr.strip()[:_REASON_LIMIT], self._secrets())

    @staticmethod
    def _to_message(item: Any) -> MailboxMessage:
        if not isinstance(item, dict):
            raise MailboxAccessError(
                AcquisitionFetchState.FAILED, "unexpected mailbox message schema (not an object)"
            )
        message_id = item.get("message_id") or item.get("id")
        if not isinstance(message_id, str) or not message_id:
            raise MailboxAccessError(
                AcquisitionFetchState.FAILED, "unexpected mailbox message schema (missing id)"
            )
        thread_id = item.get("thread_id") or item.get("threadId")
        raw_labels = item.get("labels")
        labels = [str(label) for label in raw_labels] if isinstance(raw_labels, list) else []
        sender = item.get("sender") or item.get("from") or ""
        body = item.get("body")
        raw_sent_at = item.get("date") or item.get("sent_at") or item.get("sentAt")
        if raw_sent_at is None:
            raise MailboxAccessError(
                AcquisitionFetchState.FAILED, "unexpected mailbox message schema (missing date)"
            )
        try:
            return MailboxMessage(
                message_id=message_id,
                thread_id=str(thread_id) if thread_id else None,
                sent_at=raw_sent_at,
                sender=str(sender),
                subject=str(item.get("subject") or ""),
                body=str(body) if body is not None else "",
                labels=labels,
            )
        except ValidationError as exc:
            # A message without a usable date (or another malformed field) is
            # not silently dated/defaulted: the whole search fails closed.
            raise MailboxAccessError(
                AcquisitionFetchState.FAILED,
                f"unexpected mailbox message schema: {exc.errors()[0].get('msg', 'invalid')}",
            ) from exc
