# S-07 X4 Restore Procedure — cron wrapper and production tree

Runbook for rolling back the S-07 X4 deployment:

- **Wrapper**: dry-run cron wrapper installed at `/root/.hermes/scripts/s07_testnet_daily.sh`
  (current sha256 `3ba20004cfc800a579ebfdd5555e48a5c67caae27f1f8e6f98bc3e4ff5cde300`;
  previous version kept at `/root/.hermes/scripts/s07_testnet_daily.sh.bak-t86d669ed-20261109T151406Z`,
  sha256 `aca5a4cefd241ca28ef75805322cbbbdc47d3f1e79bfdf948f977ad7830a4b1d`).
- **Production tree**: deployment commit `b03eacf2f3e444972905ef3eafba243d5e582056`
  (branch `agent/commercial-foundation`, parent `3ad555dc52f9ac11958dff04a3eddc496bd0e5b2`,
  40 files / 4960 insertions) in `/root/current-work/yowayowa-investor`, with a full
  pre-deployment tar backup at `/root/current-work/backup-t_ba209c6f/production-before.tar.gz`.

All hashes and expected outputs below were **measured** on the VPS host (`ubuntu`) on
2026-11-10 (UTC); none are placeholders. This file is untracked in the repo by design —
it is not part of any deployment commit.

## 0. Preconditions — read before any restore step

The daily cron job must stay paused for every procedure in this document.
Check it first, and re-check it after each restore step (§4):

```
jq '[.. | objects | select(.id? == "12f8248e71a5") | {id, enabled, state, paused_at, script}]' \
  /root/.hermes/cron/jobs.json
```

Expected output (measured 2026-11-10):

```json
[
  {
    "id": "12f8248e71a5",
    "enabled": false,
    "state": "paused",
    "paused_at": "2026-11-09T08:03:47.957228+09:00",
    "script": "s07_testnet_daily.sh"
  }
]
```

If `enabled` is `true` or `state` is not `paused`: **stop**. Pause the job via the operator
console before touching any file; do not run a restore while the job is enabled.
This document intentionally contains **no resume/unpause step** — resuming the job is a
separate decision outside this runbook.

## 1. Reference values (all measured 2026-11-10)

| Item | Path / command | Value |
|---|---|---|
| Installed wrapper — current (deployed) | `/root/.hermes/scripts/s07_testnet_daily.sh` | sha256 `3ba20004cfc800a579ebfdd5555e48a5c67caae27f1f8e6f98bc3e4ff5cde300` |
| Installed wrapper — previous (backup) | `/root/.hermes/scripts/s07_testnet_daily.sh.bak-t86d669ed-20261109T151406Z` | sha256 `aca5a4cefd241ca28ef75805322cbbbdc47d3f1e79bfdf948f977ad7830a4b1d` |
| Wrapper copy in production tree (tracked in `b03eacf2`, blob `5ce6d8f3c71e5409d76c0877b851ad11e0cc13b0`, mode 100755) | `/root/current-work/yowayowa-investor/scripts/s07_forward/s07_testnet_daily.sh` | sha256 `3ba20004cfc800a579ebfdd5555e48a5c67caae27f1f8e6f98bc3e4ff5cde300` |
| Forward runner (deployed state) | `/root/current-work/yowayowa-investor/src/yowayowa/hl_exec/s07_forward/runner.py` | sha256 `d89eeffbfcd7cb8b2ee7d5f3b94eb261b815cd63b08c8088482eb6fcf91b16c3` |
| Order gate (deployed state) | `/root/current-work/yowayowa-investor/src/yowayowa/hl_exec/s07_forward/order_gate.py` | sha256 `ece690ff1c8ae0bc88fac78ae45039d6043fc64752c63afba089a14bb145b6b8` |
| Stop marker (unchanged, fail-closed) | `/root/current-work/yowayowa-investor/scripts/hl_testnet_s07/s07_daily_forward.py` | sha256 `88f5b3d6bdd2c8e830410c9b2eb095e7a0de91c01f3a558859f913eb8e872338` |
| Deployed commit | `git -C /root/current-work/yowayowa-investor rev-parse HEAD` | `b03eacf2f3e444972905ef3eafba243d5e582056` on branch `agent/commercial-foundation` (parent `3ad555dc52f9ac11958dff04a3eddc496bd0e5b2`, 40 files / 4960 insertions) |
| Pre-deployment backup tar | `/root/current-work/backup-t_ba209c6f/production-before.tar.gz` | sha256 `4a3b74f3ec13b4f8094d755c30428727e9f97c33b5d8826818415ddc578f7f54`, 558,696,624 bytes, 59,602 entries |
| Backup-time HEAD / branch | `/root/current-work/backup-t_ba209c6f/head.before` / `branch.before` | `3ad555dc52f9ac11958dff04a3eddc496bd0e5b2` / `agent/commercial-foundation` |
| Backup-time status snapshot | `/root/current-work/backup-t_ba209c6f/status.before` | `git status --porcelain -uall` at backup time (42 lines, measured) |

Notes:

- The installed wrapper (`/root/.hermes/scripts/`) and the tree copy
  (`scripts/s07_forward/`) were byte-identical at deployment time (`cmp` exit 0).
  Restoring one does **not** restore the other — they are separate files in separate locations.
- `src/yowayowa/hl_exec/s07_forward/` (runner, order_gate, `__init__.py`) and
  `tests/fixtures/s07_authenticity/real/` did **not exist** in the tree before commit
  `b03eacf2` (measured: neither appears in `tar -tzf` of the backup). After a tree
  rollback, the runner/order_gate check is therefore "absent", not a hash match.

## 2. Restore the previous cron wrapper (expect `aca5a4ce…`)

```
cp -p /root/.hermes/scripts/s07_testnet_daily.sh.bak-t86d669ed-20261109T151406Z \
      /root/.hermes/scripts/s07_testnet_daily.sh
sha256sum /root/.hermes/scripts/s07_testnet_daily.sh
# expected: aca5a4cefd241ca28ef75805322cbbbdc47d3f1e79bfdf948f977ad7830a4b1d
bash -n /root/.hermes/scripts/s07_testnet_daily.sh   # must exit 0
```

Keep the `.bak-…` file in place after copying — it is the only copy of the previous
wrapper. This step restores only the installed cron wrapper: the tracked tree copy
`scripts/s07_forward/s07_testnet_daily.sh` (commit `b03eacf2`) still hashes
`3ba20004…`, which is expected and leaves `git status` unchanged.
Re-run the cron check from §0 afterwards.

## 3. Restore the production tree from the tar backup (rollback of `b03eacf2`)

The archive contains `yowayowa-investor/` **including `.git`**, so extracting it restores
working tree, index, HEAD and branch to the pre-deployment snapshot in one step.

**Warning**: after extraction, commit `b03eacf2` is no longer reachable (it was created
after the backup, so the restored `.git` does not contain it, and `git show b03eacf2`
will fail). The hash is preserved in this document and in the deployment evidence
(`/root/.hermes/kanban/boards/yowayowa/attachments/t_ba209c6f/deployment-evidence.md`);
the deployment is re-creatable from branch `agent/s07-prod-runner` (commit
`64b5078103636301817e2454ced40ae2c4e56d0a`, which **does** survive in the restored
`.git`) using the documented pathspec-checkout method.

### 3.1 Verify the backup before touching anything

```
sha256sum /root/current-work/backup-t_ba209c6f/production-before.tar.gz
# expected: 4a3b74f3ec13b4f8094d755c30428727e9f97c33b5d8826818415ddc578f7f54
```

If the hash differs, **stop** — never extract an unverified archive.

### 3.2 Record the state you are leaving (incident log)

Before extraction, save `git status --porcelain -uall` to a safe location outside the tree as `status.leaving`, compare it to `/root/current-work/backup-t_ba209c6f/status.before`, and **stop** if there are any differences beyond the two explicitly documented D8 deployment/document changes. Status alone cannot detect altered content of already-dirty files or ignored files: preserve and independently compare all such data and later commits before authorizing extraction; if preservation cannot be established, **do not extract**.

```
git -C /root/current-work/yowayowa-investor status --porcelain -uall > /root/current-work/backup-t_ba209c6f/status.leaving
diff /root/current-work/backup-t_ba209c6f/status.before /root/current-work/backup-t_ba209c6f/status.leaving
```

The separately retained historical cron backup is `/root/.hermes/kanban/boards/yowayowa/attachments/t_04549e19/jobs.json.bak` (90,264 bytes; sha256 `5a47c08e0bf8170de6fc32ddd16c07882b9f24350bca0e491dc7ea68d0036eb7`). Verify it by `sha256sum` before use. It covers the whole scheduler and is not permission to overwrite current jobs.json or resume the job; restoration requires a separately authorized, current-state-safe procedure.

```
cd /root/current-work/yowayowa-investor
git rev-parse HEAD          # expected before rollback: b03eacf2f3e444972905ef3eafba243d5e582056
git branch --show-current   # agent/commercial-foundation
df -h / | tail -1           # archive is 559 MiB; extraction is in-place (overwrite), keep some headroom
```

### 3.3 Extract in place (cron must read `paused` in §0 first)

```
tar -xzf /root/current-work/backup-t_ba209c6f/production-before.tar.gz -C /root/current-work
```

### 3.4 Verify HEAD / branch

```
cd /root/current-work/yowayowa-investor
git rev-parse HEAD          # expected: 3ad555dc52f9ac11958dff04a3eddc496bd0e5b2 (= head.before)
git branch --show-current   # expected: agent/commercial-foundation (= branch.before)
```

### 3.5 Remove the 39 files commit `b03eacf2` added

`tar` cannot delete files that are absent from the archive, so the 39 deployed files
(every commit path except the wrapper, which existed untracked at backup time) remain on
disk as untracked leftovers. They are exactly: the 4 brand-new directories
`src/yowayowa/hl_exec/s07_forward/` (3 files), `src/yowayowa/hl_exec/s07_authenticity/`
(7 files), `tests/fixtures/s07_authenticity/real/` (10 files),
`docs/evidence/s07_x3_forward_20261109/` (7 files) = 27 files, plus 12 individual files
(6 s07_x3 docs + 6 s07 tests) = **39 files**. Dry-run first:

```
cd /root/current-work/yowayowa-investor
git clean -nfd -- \
  src/yowayowa/hl_exec/s07_forward \
  src/yowayowa/hl_exec/s07_authenticity \
  tests/fixtures/s07_authenticity/real \
  docs/evidence/s07_x3_forward_20261109 \
  docs/evidence/S07_X3_SCOPE_RULING_20261109.md \
  docs/s07_x3_contract.md \
  docs/s07_x3_evidence.md \
  docs/s07_x3_f1_fix.md \
  docs/s07_x3_red_log.txt \
  docs/s07_x3_review.md \
  tests/test_s07_day_normalization.py \
  tests/test_s07_direct_path_evidence.py \
  tests/test_s07_forward_daily.py \
  tests/test_s07_forward_dryrun.py \
  tests/test_s07_forward_runner_guards.py \
  tests/test_s07_runner_acceptance.py
```

Expected: exactly 39 lines, all under the 16 pathspecs above — nothing else.
The full 40-path list (wrapper included) is
`/root/.hermes/kanban/boards/yowayowa/attachments/t_ba209c6f/intended-paths.txt`.
If the dry-run shows anything outside these pathspecs, **stop** and investigate.
Then apply, with the same 16 pathspecs:

```
git clean -fd -- <the same 16 pathspecs as above>
```

Confirm the deployed package is gone:

```
ls src/yowayowa/hl_exec/s07_forward      # expected: No such file or directory
ls src/yowayowa/hl_exec/s07_authenticity # expected: No such file or directory
```

### 3.6 Wrapper state after the tree rollback

- Tree copy `scripts/s07_forward/s07_testnet_daily.sh` is back (from the tar) as an
  **untracked** file, sha256 `3ba20004cfc800a579ebfdd5555e48a5c67caae27f1f8e6f98bc3e4ff5cde300`.
- The installed wrapper `/root/.hermes/scripts/s07_testnet_daily.sh` is **not** touched by
  the tar (it lives outside the repo). If the pre-X4 wrapper `aca5a4ce…` is wanted there
  as well, apply §2 on top of §3.

### 3.7 Off-site (Drime) source

Measured 2026-11-10: no copy of `production-before.tar.gz` exists on the `Drime:` remote
(`Backups/`, `yowayowa_bundle/`, `hermes-archive/` checked; `yowayowa_bundle/` holds only
older snapshot bundles and the X3 evidence tar). The local tar is the only verified
backup source today. If an off-site copy is created later, it is usable **only after**
`sha256sum` returns `4a3b74f3ec13b4f8094d755c30428727e9f97c33b5d8826818415ddc578f7f54`
(`rclone copy Drime:<path> /root/current-work/backup-t_ba209c6f/`, then re-hash before §3.3).

## 4. Cron check (repeat after every restore step)

Re-run the jq command from §0. Expected: unchanged — `enabled=false`, `state=paused`,
`paused_at=2026-11-09T08:03:47.957228+09:00`. No resume step exists in this document.

## 5. Rollback verification checklist

### State D — deployed (no rollback applied)

| # | Check | Command | Expected (measured) |
|---|---|---|---|
| D1 | Installed wrapper | `sha256sum /root/.hermes/scripts/s07_testnet_daily.sh` | `3ba20004cfc800a579ebfdd5555e48a5c67caae27f1f8e6f98bc3e4ff5cde300` |
| D2 | Tree wrapper | `sha256sum /root/current-work/yowayowa-investor/scripts/s07_forward/s07_testnet_daily.sh` | `3ba20004cfc800a579ebfdd5555e48a5c67caae27f1f8e6f98bc3e4ff5cde300`; `cmp` with D1 file → identical |
| D3 | Runner | `sha256sum /root/current-work/yowayowa-investor/src/yowayowa/hl_exec/s07_forward/runner.py` | `d89eeffbfcd7cb8b2ee7d5f3b94eb261b815cd63b08c8088482eb6fcf91b16c3` |
| D4 | Order gate | `sha256sum /root/current-work/yowayowa-investor/src/yowayowa/hl_exec/s07_forward/order_gate.py` | `ece690ff1c8ae0bc88fac78ae45039d6043fc64752c63afba089a14bb145b6b8` |
| D5 | HEAD / branch | `git -C /root/current-work/yowayowa-investor rev-parse HEAD && git -C /root/current-work/yowayowa-investor branch --show-current` | `b03eacf2f3e444972905ef3eafba243d5e582056` / `agent/commercial-foundation` |
| D6 | Cron | §0 jq | `enabled=false`, `state=paused` |
| D7 | Stop marker | `sha256sum /root/current-work/yowayowa-investor/scripts/hl_testnet_s07/s07_daily_forward.py` | `88f5b3d6bdd2c8e830410c9b2eb095e7a0de91c01f3a558859f913eb8e872338` |
| D8 | No unintended git changes | `cd /root/current-work/yowayowa-investor && git status --porcelain --untracked-files=all \| diff /root/current-work/backup-t_ba209c6f/status.before -` | exactly two lines (measured 2026-11-10): removed `?? scripts/s07_forward/s07_testnet_daily.sh` (the wrapper became tracked), and added `?? docs/s07_x4_restore.md` (this document, while it is untracked). Any other difference is unintended — investigate before proceeding. |

### State R — tree rolled back to `3ad555dc…` (§3 applied; §2 optional, noted)

| # | Check | Command | Expected |
|---|---|---|---|
| R1 | HEAD / branch | same commands as D5 | `3ad555dc52f9ac11958dff04a3eddc496bd0e5b2` / `agent/commercial-foundation` |
| R2 | Installed wrapper | `sha256sum /root/.hermes/scripts/s07_testnet_daily.sh` | `aca5a4cefd241ca28ef75805322cbbbdc47d3f1e79bfdf948f977ad7830a4b1d` if §2 applied; `3ba20004cfc800a579ebfdd5555e48a5c67caae27f1f8e6f98bc3e4ff5cde300` if only the tree was rolled back |
| R3 | Tree wrapper | `sha256sum /root/current-work/yowayowa-investor/scripts/s07_forward/s07_testnet_daily.sh` | `3ba20004cfc800a579ebfdd5555e48a5c67caae27f1f8e6f98bc3e4ff5cde300`, untracked (`?? scripts/s07_forward/s07_testnet_daily.sh` present in `git status --porcelain -uall`) |
| R4 | Runner / order gate | `ls /root/current-work/yowayowa-investor/src/yowayowa/hl_exec/s07_forward/` | `No such file or directory` (package removed by §3.5; hash checks N/A) |
| R5 | Stop marker | same command as D7 | `88f5b3d6bdd2c8e830410c9b2eb095e7a0de91c01f3a558859f913eb8e872338` unchanged |
| R6 | Cron | §0 jq | `enabled=false`, `state=paused`, `paused_at` unchanged |
| R7 | No unintended git changes | same diff command as D8 | **empty diff** — status identical to backup-time `status.before`. Transient junk (`.coverage.ubuntu.pid*`, `.t45probe.txt`) was already present at backup time and is inside the snapshot; a **new** line of any kind is a finding, not something to ignore |

## 6. Document history

- 2026-11-10: created by task t_2d736566. No prior restore document existed
  (measured: no `*restore*` file anywhere in the repo; `git log --all -- 'docs/*restore*'`
  empty). Content: wrapper current/previous hashes (§1), wrapper restore commands (§2),
  production-tree tar restore + rollback of `b03eacf2` (§3), cron paused checks with no
  resume step (§0/§4), and the State D / State R verification checklist (§5).
