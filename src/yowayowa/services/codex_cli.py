from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from yowayowa.config import Settings
from yowayowa.research_models import CodexCLIStatus

_CODEX_BILLING_ENV = {
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "CODEX_ACCESS_TOKEN",
    "OPENAI_IDENTITY_TOKEN_FILE",
    "OPENAI_FEDERATION_RULE_ID",
}


@dataclass(frozen=True)
class CodexStructuredResult:
    result: dict[str, Any]
    credential: str | None = None


def _binary() -> str | None:
    return shutil.which("codex")


def _subscription_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in _CODEX_BILLING_ENV:
        env.pop(key, None)
    return env


def _bridge_url(settings: Settings, path: str) -> str:
    base = (settings.codex_bridge_url or "").rstrip("/")
    if not base:
        raise RuntimeError("Hosted Codex bridge is not configured")
    return f"{base}/{path.lstrip('/')}"


def codex_cli_status(settings: Settings) -> CodexCLIStatus:
    if settings.codex_bridge_url:
        return CodexCLIStatus(
            enabled=True,
            installed=True,
            authenticated=False,
            mode="hosted_bridge",
            reason="ChatGPT login is checked per browser session.",
        )
    if not settings.codex_cli_enabled:
        return CodexCLIStatus(
            enabled=False,
            installed=False,
            authenticated=False,
            mode="disabled",
            reason="Codex CLI is disabled on this deployment.",
        )
    binary = _binary()
    if binary is None:
        return CodexCLIStatus(
            enabled=True,
            installed=False,
            authenticated=False,
            mode="local_cli",
            reason="Codex CLI is not installed.",
        )

    version: str | None = None
    try:
        completed = subprocess.run(
            [binary, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            env=_subscription_env(),
        )
        version = (completed.stdout or completed.stderr).strip() or None
    except (OSError, subprocess.TimeoutExpired):
        version = None

    try:
        completed = subprocess.run(
            [binary, "login", "status"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
            env=_subscription_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CodexCLIStatus(
            enabled=True,
            installed=True,
            authenticated=False,
            version=version,
            mode="local_cli",
            reason=f"Could not inspect Codex login: {type(exc).__name__}",
        )

    summary = " ".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    chatgpt = completed.returncode == 0 and "chatgpt" in summary.casefold()
    reason = None
    if completed.returncode != 0:
        reason = "Codex CLI is installed but not logged in."
    elif not chatgpt:
        reason = (
            "Codex is authenticated, but not with ChatGPT. Sign in with ChatGPT "
            "to use subscription allowance instead of API billing."
        )
    return CodexCLIStatus(
        enabled=True,
        installed=True,
        authenticated=chatgpt,
        version=version,
        auth_summary=summary or None,
        mode="local_cli",
        reason=reason,
    )


def codex_bridge_session_status(
    settings: Settings,
    *,
    credential: str,
    session_id: str,
) -> CodexCLIStatus:
    try:
        response = httpx.post(
            _bridge_url(settings, "/session-status"),
            headers={"x-yowayowa-codex-session": session_id},
            json={"credential": credential},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError("Hosted Codex session check failed") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Hosted Codex bridge returned invalid status")
    return CodexCLIStatus.model_validate(payload)


def start_codex_login(settings: Settings) -> None:
    status = codex_cli_status(settings)
    if status.mode == "hosted_bridge":
        raise RuntimeError("Hosted Codex uses the browser device-code flow")
    if not status.enabled:
        raise RuntimeError(status.reason or "Codex CLI is disabled")
    binary = _binary()
    if binary is None:
        raise RuntimeError("Codex CLI is not installed")

    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": _subscription_env(),
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([binary, "login"], **kwargs)


def run_codex_structured(
    settings: Settings,
    *,
    prompt: str,
    schema: dict[str, Any],
    model: str | None = None,
    credential: str | None = None,
    session_id: str | None = None,
) -> CodexStructuredResult:
    if settings.codex_bridge_url:
        if not credential or not session_id:
            raise RuntimeError("ChatGPT login is required for hosted Codex")
        try:
            response = httpx.post(
                _bridge_url(settings, "/structured"),
                headers={"x-yowayowa-codex-session": session_id},
                json={
                    "credential": credential,
                    "prompt": prompt,
                    "schema": schema,
                    "model": model,
                },
                timeout=settings.codex_cli_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("Hosted Codex execution failed") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict):
            raise RuntimeError("Hosted Codex bridge returned invalid structured output")
        refreshed = payload.get("credential")
        return CodexStructuredResult(
            result=payload["result"],
            credential=str(refreshed) if refreshed else credential,
        )

    status = codex_cli_status(settings)
    if not status.authenticated:
        raise RuntimeError(status.reason or "Codex is not logged in with ChatGPT")
    binary = _binary()
    if binary is None:
        raise RuntimeError("Codex CLI is not installed")

    with tempfile.TemporaryDirectory(prefix="yowayowa-codex-") as temp:
        root = Path(temp)
        schema_path = root / "schema.json"
        output_path = root / "result.json"
        schema_path.write_text(
            json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        command = [
            binary,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--sandbox",
            "read-only",
            "--ask-for-approval",
            "never",
            "--skip-git-repo-check",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
        ]
        resolved_model = (model or "").strip()
        if resolved_model and resolved_model.casefold() not in {"auto", "default"}:
            command.extend(["--model", resolved_model])
        command.append("-")

        try:
            completed = subprocess.run(
                command,
                input=prompt,
                check=False,
                capture_output=True,
                text=True,
                timeout=settings.codex_cli_timeout_seconds,
                cwd=root,
                env=_subscription_env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Codex CLI timed out") from exc
        except OSError as exc:
            raise RuntimeError(f"Could not start Codex CLI: {exc}") from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            if len(detail) > 3000:
                detail = detail[-3000:]
            raise RuntimeError(f"Codex CLI failed: {detail or completed.returncode}")
        try:
            raw = output_path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Codex CLI returned invalid structured output") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("Codex CLI returned a non-object response")
        return CodexStructuredResult(result=parsed)
