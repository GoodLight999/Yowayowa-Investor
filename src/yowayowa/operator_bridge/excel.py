from __future__ import annotations

import os
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol


class MacroRunner(Protocol):
    def run_macro(self, name: str, args: Sequence[object]) -> object: ...


class WorksheetRunner(Protocol):
    def read_scalar_formula(self, formula: str) -> object: ...

    def read_table_formula(self, formula: str) -> list[list[object]]: ...


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

    def _scratch_sheet(self) -> Any:
        book = self._resolve_book()
        name = "__YOWAYOWA_RSS__"
        try:
            sheet = book.sheets[name]
        except Exception:
            try:
                sheet = book.sheets.add(name, after=book.sheets[-1])
                sheet.visible = False
            except Exception as exc:
                raise ExcelBridgeUnavailable("Could not create the RSS scratch worksheet") from exc
        return sheet

    @staticmethod
    def _formula(value: str) -> str:
        formula = value.strip()
        if not formula.startswith("="):
            formula = "=" + formula
        return formula

    def run_macro(self, name: str, args: Sequence[object]) -> object:
        book = self._resolve_book()
        try:
            return book.app.macro(name)(*args)
        except Exception as exc:
            raise ExcelBridgeUnavailable(f"Excel macro call failed: {name}") from exc

    def read_scalar_formula(self, formula: str) -> object:
        sheet = self._scratch_sheet()
        try:
            sheet.clear_contents()
            sheet.range("A1").formula = self._formula(formula)
            sheet.book.app.calculate()
            for _ in range(20):
                value = sheet.range("A1").value
                if value is not None:
                    return value
                time.sleep(0.05)
            return sheet.range("A1").value
        except Exception as exc:
            raise ExcelBridgeUnavailable("RSS scalar worksheet function failed") from exc

    def read_table_formula(self, formula: str) -> list[list[object]]:
        sheet = self._scratch_sheet()
        try:
            sheet.clear_contents()
            sheet.range("A1").formula = self._formula(formula)
            sheet.book.app.calculate()
            previous_shape: tuple[int, int] | None = None
            stable = 0
            values: object = None
            for _ in range(30):
                region = sheet.range("A1").current_region
                shape = (int(region.rows.count), int(region.columns.count))
                values = region.value
                if shape == previous_shape and shape != (1, 1):
                    stable += 1
                    if stable >= 2:
                        break
                else:
                    stable = 0
                    previous_shape = shape
                time.sleep(0.1)
            if values is None:
                return []
            if not isinstance(values, list):
                return [[values]]
            if values and not isinstance(values[0], list):
                return [list(values)]
            return [list(row) for row in values]
        except Exception as exc:
            raise ExcelBridgeUnavailable("RSS table worksheet function failed") from exc
