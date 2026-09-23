# Agent environment: shared tool venv and verification

This document defines how autonomous agents construct, share, and use the
Python tool virtualenv of this repository. It exists because concurrent
tasks working on separate worktrees of the same repository must never
corrupt each other's verification results.

## Background: the editable .pth race (2026-09-24 hotspot)

`uv sync` (and `uv run`, which runs an implicit sync) installs this project
into the venv as an **editable install**. Editable installs materialize as a
simple-path `.pth` file
(`.venv/lib/python3.13/site-packages/_editable_impl_yowayowa_investor.pth`)
pointing at the `src/` of **the tree the sync was executed from**. When two
tasks share one venv and sync from different trees, the `.pth` ends up
pointing at whichever tree synced last. Verification in a lagging tree then
imports stale code: missing new modules surface as `ModuleNotFoundError`,
missing new files surface as unexpected ruff failures (both observed on
2026-09-24, FX Phase2 review).

## Rules

1. **The venv holds tools only; project code never enters the venv.** No
   editable install of `yowayowa-investor` may exist. The shared tool venv is
   the main checkout's `.venv` (`/root/current-work/yowayowa-investor/.venv`,
   the Makefile's default `$(V) = $(CURDIR)/.venv`). Do not point `V` at any
   agent scratch directory (scratch dirs are pruned).
   Worktrees do not contain `.venv` (git does not track it); from a worktree
   pass the main checkout's venv explicitly:
   `make V=/root/current-work/yowayowa-investor/.venv <target>`. Omitting
   `V=` in a worktree bootstraps a private full copy (~0.5 GB); the host
   disk runs near 95% full, so always pass `V=` from worktrees.
2. **Every process resolves the project from its own tree.** All Makefile
   targets export `PYTHONPATH=$(CURDIR)/src`, which takes precedence over
   `site-packages` and over any `.pth`-appended path. Verification results
   therefore cannot be influenced by shared mutable venv state.
3. **Run verification only in your own checkout/worktree** (never on another
   task's live tree). Two trees may run `make verify` simultaneously against
   the shared venv safely.
4. **Sync is `--no-install-project` only.** The only sanctioned dependency
   command is `make install` (`uv sync --extra dev --extra operator-ir
   --no-install-project`), run once at initial setup or after `uv.lock`
   changes. Never run a bare `uv sync` / `uv run` against this repository's
   venv — both re-introduce the editable `.pth`. If a venv is found polluted
   (site-packages contains `yowayowa` or the editable `.pth`), remove it with
   `uv pip uninstall --python <venv>/bin/python yowayowa-investor` and
   confirm `--frozen --no-install-project` sync is a no-op.
5. **`make install` is for initial venv construction only.** Do not re-run it
   before every verification; a fresh clone or worktree needs no manual step
   because the Makefile's order-only `bootstrap` prerequisite creates the
   shared venv on first use (idempotent under uv's environment lock).

## Makefile contract

- `make verify` = lint → typecheck → test → openapi, all calling
  `$(V)/bin/<tool>` directly (`ruff`, `mypy`, `pytest`,
  `python -m yowayowa.cli_shim`).
- `make dev` runs `$(V)/bin/python -m uvicorn yowayowa.api.app:app --reload`;
  the exported `PYTHONPATH` is inherited by uvicorn's reload subprocess, so
  the server also resolves `yowayowa` from the current tree.
- `make install` (initial setup, `--no-install-project`).

## GitHub CI

CI (`.github/workflows/ci.yml`) intentionally keeps `uv sync --extra dev` +
`uv run`: every runner checks out a disposable tree, so the cross-worktree
race structurally cannot occur there. Do not "port" the local direct-call
rules into CI.

## History

- 2026-09-24: venv migrated to editable-free state (`uv pip uninstall
  yowayowa-investor`, verified `uv sync --frozen --no-install-project` is a
  no-op) as hotspot remedy for task t_9803f6b1.
