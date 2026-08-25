from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _load_app() -> Callable[..., Any]:
    from yowayowa.cli_entry import app

    return app


def main() -> None:
    try:
        app = _load_app()
    except ModuleNotFoundError as exc:
        if exc.name in {"typer", "rich"}:
            raise SystemExit(
                "Yowayowa CLI requires optional CLI dependencies. "
                "Install with: pip install 'yowayowa-investor[cli]'"
            ) from exc
        raise
    app()


__all__ = ["main"]
