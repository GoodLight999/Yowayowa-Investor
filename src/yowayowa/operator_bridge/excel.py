from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol


class MacroRunner(Protocol):
    def run_macro(self, name: str, args: Sequence[object]) -> object: ...


class ExcelBridgeUnavailable(RuntimeError):
    pass


class XlwingsMacroRunner:
    """Call an Excel/VBA macro on the operator's Windows machine.

    The implementation deliberately imports xlwings lazily so the normal hosted
    application and Linux CI do not acquire a Windows/Excel dependency.
    """

    def __init__(self, workbook: str | Path | None = None) -> None:
        self._workbook = Path(workbook).expanduser().resolve() if workbook else None

    @staticmethod
    def _xlwings() -> Any:
        try:
            import xlwings as xw  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ExcelBridgeUnavailable(
                "The Windows Operator Bridge requires the optional xlwings dependency"
            ) from exc
        return xw

    def _resolve_book(self) -> Any:
        xw = self._xlwings()
        try:
            apps = list(xw.apps)
        except Exception as exc:
            raise ExcelBridgeUnavailable("Excel is not running or is not reachable") from exc
        if not apps:
            raise ExcelBridgeUnavailable(
                "Excel is not running. Start Excel and MARKET SPEED II RSS first."
            )

        if self._workbook is None:
            try:
                book = xw.apps.active.books.active
            except Exception as exc:
                raise ExcelBridgeUnavailable("No active Excel workbook is available") from exc
            if book is None:
                raise ExcelBridgeUnavailable("No active Excel workbook is available")
            return book

        wanted = os.path.normcase(str(self._workbook))
        for app in apps:
            try:
                books = list(app.books)
            except Exception:
                continue
            for book in books:
                try:
                    if os.path.normcase(str(Path(book.fullname).resolve())) == wanted:
                        return book
                except Exception:
                    continue
        raise ExcelBridgeUnavailable(
            f"The configured workbook is not open in Excel: {self._workbook}"
        )

    def run_macro(self, name: str, args: Sequence[object]) -> object:
        book = self._resolve_book()
        try:
            return book.app.macro(name)(*args)
        except Exception as exc:
            raise ExcelBridgeUnavailable(f"Excel macro call failed: {name}") from exc
