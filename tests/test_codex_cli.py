from __future__ import annotations

from subprocess import CompletedProcess

from yowayowa.config import Settings
from yowayowa.services import codex_cli


def test_codex_status_accepts_chatgpt_login_and_never_needs_api_key(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(codex_cli.shutil, "which", lambda _name: "/usr/bin/codex")

    def fake_run(command, **_kwargs):  # type: ignore[no-untyped-def]
        if "--version" in command:
            return CompletedProcess(command, 0, stdout="codex-cli 0.153.0\n", stderr="")
        return CompletedProcess(
            command,
            0,
            stdout="Logged in using ChatGPT\n",
            stderr="",
        )

    monkeypatch.setattr(codex_cli.subprocess, "run", fake_run)
    status = codex_cli.codex_cli_status(Settings(database_url="sqlite:///:memory:"))

    assert status.installed is True
    assert status.authenticated is True
    assert status.version == "codex-cli 0.153.0"
    assert "ChatGPT" in (status.auth_summary or "")


def test_codex_status_rejects_non_chatgpt_auth(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(codex_cli.shutil, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(
        codex_cli.subprocess,
        "run",
        lambda command, **_kwargs: CompletedProcess(
            command,
            0,
            stdout="Logged in using API key\n",
            stderr="",
        ),
    )

    status = codex_cli.codex_cli_status(Settings(database_url="sqlite:///:memory:"))

    assert status.installed is True
    assert status.authenticated is False
    assert status.reason is not None
    assert "subscription allowance" in status.reason


def test_codex_structured_exec_strips_api_billing_environment(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(codex_cli.shutil, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "must-not-leak-either")
    captured: dict[str, object] = {}

    def fake_status(_settings):  # type: ignore[no-untyped-def]
        from yowayowa.research_models import CodexCLIStatus

        return CodexCLIStatus(
            enabled=True,
            installed=True,
            authenticated=True,
            auth_summary="Logged in using ChatGPT",
        )

    def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        captured["command"] = command
        captured["env"] = kwargs["env"]
        output_index = command.index("--output-last-message") + 1
        from pathlib import Path

        Path(command[output_index]).write_text(
            '{"answer":"ok","tool_calls":[]}',
            encoding="utf-8",
        )
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(codex_cli, "codex_cli_status", fake_status)
    monkeypatch.setattr(codex_cli.subprocess, "run", fake_run)

    result = codex_cli.run_codex_structured(
        Settings(database_url="sqlite:///:memory:"),
        prompt="hello",
        schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
        model="default",
    )

    assert result.result["answer"] == "ok"
    env = captured["env"]
    assert isinstance(env, dict)
    assert "OPENAI_API_KEY" not in env
    assert "CODEX_ACCESS_TOKEN" not in env
    command = captured["command"]
    assert isinstance(command, list)
    assert "--model" not in command
    assert "--sandbox" in command
    assert "read-only" in command



def test_codex_status_uses_hosted_bridge_without_local_binary() -> None:
    status = codex_cli.codex_cli_status(
        Settings(
            database_url="sqlite:///:memory:",
            codex_cli_enabled=False,
            codex_bridge_url="https://codex.internal",
        )
    )

    assert status.enabled is True
    assert status.installed is True
    assert status.authenticated is False
    assert status.mode == "hosted_bridge"


def test_hosted_codex_refreshes_sealed_credential(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, object] = {}

    class BridgeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "result": {"answer": "hosted", "tool_calls": []},
                "credential": "sealed-refreshed",
            }

    def fake_post(url: str, **kwargs: object) -> BridgeResponse:
        captured["url"] = url
        captured.update(kwargs)
        return BridgeResponse()

    monkeypatch.setattr(codex_cli.httpx, "post", fake_post)
    result = codex_cli.run_codex_structured(
        Settings(
            database_url="sqlite:///:memory:",
            codex_bridge_url="https://codex.internal",
        ),
        prompt="hello",
        schema={"type": "object"},
        model="default",
        credential="sealed-old",
        session_id="browser-session-0123456789",
    )

    assert result.result["answer"] == "hosted"
    assert result.credential == "sealed-refreshed"
    assert captured["url"] == "https://codex.internal/structured"
    assert captured["headers"] == {
        "x-yowayowa-codex-session": "browser-session-0123456789"
    }
