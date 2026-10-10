"""Import shim for the demo paper-loop (v1.1). The implementation lives in
``yowayowa.paperloop`` (event journal) and ``yowayowa.paperloop_guard``
(frozen threshold guard); this name exists so the loop surface reads as
``yowayowa.demo_loop.*``. It re-exports only — no logic, no second engine,
no order path."""

from __future__ import annotations

from yowayowa.paperloop import (
    BENCH_CRYPTO_BOOK,
    BENCH_EQUITY_BOOK,
    BOOK_C_BOOK,
    BOOK_C_ENABLED,
    BOOKS_ENABLED,
    CRYPTO_UNIVERSE,
    EQUITY_UNIVERSE,
    HALT_DRAWDOWN,
    MILESTONE_RULES,
    S01_BOOK,
    SCHEMA,
    ChainError,
    apply_fill,
    check_prereg,
    ensure_prereg,
    evaluate_halt,
    main,
    mark_up,
    rebuild_guard_input,
    rebuild_state,
    resolve_fills,
    run_daily_decide,
    run_daily_mtm,
    weekly_summary,
)
from yowayowa.paperloop_guard import GuardInput, check_trade

__all__ = [
    "BENCH_CRYPTO_BOOK",
    "BENCH_EQUITY_BOOK",
    "BOOKS_ENABLED",
    "BOOK_C_BOOK",
    "BOOK_C_ENABLED",
    "CRYPTO_UNIVERSE",
    "EQUITY_UNIVERSE",
    "HALT_DRAWDOWN",
    "MILESTONE_RULES",
    "S01_BOOK",
    "SCHEMA",
    "ChainError",
    "GuardInput",
    "apply_fill",
    "check_prereg",
    "check_trade",
    "ensure_prereg",
    "evaluate_halt",
    "main",
    "mark_up",
    "rebuild_guard_input",
    "rebuild_state",
    "resolve_fills",
    "run_daily_decide",
    "run_daily_mtm",
    "weekly_summary",
]
