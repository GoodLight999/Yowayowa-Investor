"""Shadow-LLM (book D v2 prototype) — configuration.

Single place to swap the model: ``ops/shadow_llm/model_config.json`` (optional)
or the ``SHADOW_LLM_*`` environment variables (env wins).

The default is a free lane: a model id ending in ``:free``.  A model id that
does not end in ``:free`` is refused unless ``SHADOW_LLM_ALLOW_PAID=1`` is set
explicitly, so an accidental edit cannot start billing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = Path(__file__).resolve().parent / "model_config.json"

DEFAULTS = {
    # free lane (measured usage.cost == 0.0, see README.md §実測)
    "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
    "base_url": "https://opengateway.gitlawb.com/v1",
    "key_env": "MINIMAX_M3_API_KEY",
    "timeout_seconds": 180,
    "max_tokens": 4096,
    "temperature": 0.0,
    "max_attempts": 2,  # 1 call + at most 1 retry (task constraint)
    "json_mode": True,  # ask the gateway for a JSON object answer
    "offline": False,
    "allow_paid": False,
}


class ConfigError(RuntimeError):
    pass


def _load_file() -> dict:
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - defensive
            raise ConfigError(f"cannot parse {CONFIG_FILE}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{CONFIG_FILE} must contain a JSON object")
        return data
    return {}


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else None


def _as_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _read_key_from_env_file(key_name: str) -> str | None:
    """Read a credential by name from the operator environment file.

    The value is never logged or returned in evidence - only used for the
    Authorization header of the single LLM call.
    """
    candidates = [
        Path(os.environ.get("SHADOW_LLM_ENV_FILE", "/root/.hermes/.env")),
        REPO_ROOT / ".env",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith(key_name + "="):
                    value = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if value:
                        return value
        except OSError:
            continue
    return None


def load() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in _load_file().items() if k in DEFAULTS})

    mapping = {
        "model": "SHADOW_LLM_MODEL",
        "base_url": "SHADOW_LLM_BASE_URL",
        "key_env": "SHADOW_LLM_KEY_ENV",
    }
    for field, env_name in mapping.items():
        value = _env(env_name)
        if value is not None:
            cfg[field] = value
    for field, env_name in (
        ("timeout_seconds", "SHADOW_LLM_TIMEOUT_SECONDS"),
        ("max_tokens", "SHADOW_LLM_MAX_TOKENS"),
        ("max_attempts", "SHADOW_LLM_MAX_ATTEMPTS"),
    ):
        value = _env(env_name)
        if value is not None:
            cfg[field] = int(value)
    for field, env_name in (
        ("offline", "SHADOW_LLM_OFFLINE"),
        ("allow_paid", "SHADOW_LLM_ALLOW_PAID"),
        ("json_mode", "SHADOW_LLM_JSON_MODE"),
    ):
        value = _env(env_name)
        if value is not None:
            cfg[field] = _as_bool(value)

    cfg["model"] = str(cfg["model"])
    cfg["base_url"] = str(cfg["base_url"]).rstrip("/")
    cfg["timeout_seconds"] = int(cfg["timeout_seconds"])
    cfg["max_tokens"] = int(cfg["max_tokens"])
    cfg["max_attempts"] = max(1, min(2, int(cfg["max_attempts"])))

    # free-lane guard (policy: never silently move onto a metered model)
    if not cfg["model"].endswith(":free") and not cfg["allow_paid"]:
        raise ConfigError(
            f"model {cfg['model']!r} is not a free lane (no ':free' suffix); "
            "set SHADOW_LLM_ALLOW_PAID=1 explicitly to use a metered model"
        )

    cfg["api_key"] = _env("SHADOW_LLM_API_KEY") or _read_key_from_env_file(cfg["key_env"])
    cfg["endpoint"] = cfg["base_url"] + "/chat/completions"
    return cfg


def journal_path() -> Path:
    root = Path(os.environ.get("SHADOW_LLM_DATA_ROOT", REPO_ROOT / "data"))
    return root / "shadow-llm" / "journal.jsonl"
