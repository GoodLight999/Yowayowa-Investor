from __future__ import annotations

import pytest

from yowayowa import cli_shim


def _missing_module(name: str) -> ModuleNotFoundError:
    error = ModuleNotFoundError(f"No module named '{name}'")
    error.name = name
    return error


def test_cli_shim_explains_missing_optional_cli_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_cli() -> object:
        raise _missing_module("typer")

    monkeypatch.setattr(cli_shim, "_load_app", missing_cli)
    with pytest.raises(SystemExit, match=r"yowayowa-investor\[cli\]"):
        cli_shim.main()


def test_cli_shim_does_not_hide_unrelated_import_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_cli() -> object:
        raise _missing_module("some_runtime_dependency")

    monkeypatch.setattr(cli_shim, "_load_app", broken_cli)
    with pytest.raises(ModuleNotFoundError, match="some_runtime_dependency"):
        cli_shim.main()
