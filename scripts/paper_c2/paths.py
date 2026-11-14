"""C2 runtime path resolution with environment overrides.

**既定値は VPS の現行レイアウト（``/root/...``）のまま固定する。** cron / systemd
がこの経路で動いているため、既定を書き換えると本番が壊れる。別ホスト（GitHub
Actions のランナーなど）で動かすときだけ、次の環境変数で根を差し替える:

- ``YOWAYOWA_DATA_ROOT``    既定 ``/root/current-work/yowayowa-investor/data``
- ``YOWAYOWA_HERMES_ROOT``  既定 ``/root/.hermes``
- ``YOWAYOWA_SECRETS_ROOT`` 既定 ``/root/.secrets``

パスを「存在するか」で判定するときは :func:`safe_is_file` / :func:`safe_is_dir`
を使う。Python 3.13 の ``Path.exists()`` / ``Path.is_dir()`` は PermissionError を
握りつぶさず送出するので、stat すら許されない既定パス（例: ランナー上の
``/root``）を素で触ると、既定値の存在確認だけで実行が落ちる。
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_DATA_ROOT = "/root/current-work/yowayowa-investor/data"
DEFAULT_HERMES_ROOT = "/root/.hermes"
DEFAULT_SECRETS_ROOT = "/root/.secrets"


def _root(env_name: str, default: str) -> Path:
    return Path(os.environ.get(env_name) or default)


def data_root() -> Path:
    """Live data tree (``data/paper_c2``, ``data/macro-observations``, ...)."""
    return _root("YOWAYOWA_DATA_ROOT", DEFAULT_DATA_ROOT)


def hermes_root() -> Path:
    """Hermes state root (cron output, logs, executions.db, ``.env``)."""
    return _root("YOWAYOWA_HERMES_ROOT", DEFAULT_HERMES_ROOT)


def secrets_root() -> Path:
    """Secret file root (mode-600 files: broker keys, kill switches)."""
    return _root("YOWAYOWA_SECRETS_ROOT", DEFAULT_SECRETS_ROOT)


def safe_is_file(path: Path) -> bool:
    """``path.is_file()`` that treats an unstatable path as "not a file"."""
    try:
        return path.is_file()
    except OSError:
        return False


def safe_is_dir(path: Path) -> bool:
    """``path.is_dir()`` that treats an unstatable path as "not a directory"."""
    try:
        return path.is_dir()
    except OSError:
        return False


def read_text_or_none(path: Path) -> str | None:
    """Read a file, returning ``None`` when it is missing or unreadable.

    PermissionError is *not* swallowed for the caller's own paths, only for
    probes of optional evidence: callers that need the distinction use
    :func:`safe_is_file` first.
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def read_env_lines(path: Path) -> list[str]:
    """``KEY=VALUE`` file lines, or ``[]`` when the file is missing/unreadable."""
    text = read_text_or_none(path)
    return text.splitlines() if text is not None else []
