# P1D Evidence — Authorized private information sources (mailbox)

Commit: `786731d` (branch `agent/p1d-private-sources`, base f4b74d4 = P1C head)
Date verified: 2026-09-23 (JST)
Verifier: cto-hephaestus (acceptance run; implementation delegated per role rules)

## 1. Acceptance criteria (task t_0b9ff63b)

| # | Criterion | Result |
|---|-----------|--------|
| 1 | ≥1 authorized connector works with provenance, tests GREEN | PASS — read-only gog mailbox connector; 123 new tests green |
| 2 | `make verify` non-decreasing vs P1C | PASS — 633 passed vs 510 at P1C (+123) |
| 3 | Failure/reauth states explicit (fail-closed) | PASS — AUTH_EXPIRED surfaces as fetch_state with empty events + note, never empty success |
| 4 | Handoff artifacts: commit SHA / real values / test results | PASS — this document |

## 2. Real-data end-to-end verification (operator mailbox, read-only)

Account: `nakanagundam@gmail.com` via `gog -j --readonly ... gmail messages search`.

### 2.1 Reader + extractor (10 messages)

- 10 real Rakuten Securities「銘柄情報通知サービス」mails fetched.
- 11 earnings events extracted, e.g.:
  - `COUR` (us) 2026-10-21 「コーセラ」 section「■決算カレンダー（2026/09/23）更新銘柄」
  - `INTC` (us) 2026-10-21 「インテル」 same section
  - `464A.T` (jp) 2026-10-14 「ＱＰＳホールディングス」 section 2026/09/07
  - `6871.T` (jp) 2026-08-12 「日本マイクロニクス」 via section-date fallback（1営業日前銘柄）
- Matches the values measured when the P1D spec was written (COUR/INTC 2026/10/21, 464A, 6871).

### 2.2 Service layer (40-message scan)

- run1: `messages_scanned=40`, `messages_new=39`, events → 70 timeline entries appended, 0 duplicates.
- run2 (within cache TTL): outcome served from cache; timeline files byte-identical (70 → 70, dup=0).
- run3 (`force_refresh=True`): `new_events=0`, timeline unchanged — fingerprints + observed-event keys make repeated runs idempotent.
- run4 (fresh service instance, same data dir, force): `new_events=0`, no re-append.

### 2.3 Provenance (timeline entry, real data)

- `provider=rakuten-sec-alerts`
- `source_url=mailbox://nakanagundam@gmail.com?q=%E6%B1%BA%E7%AE%97%E3%82%AB%E3%83%AC%E3%83%B3%E3%83%80%E3%83%BC` (query URL-encoded)
- `license_class=personal_only`
- `retrieved_at` present (UTC ISO); payload carries source_message_id / source_subject / sent_at.
- Network provenance records the mailbox exchange without headers/secrets.

### 2.4 Fail-closed behavior (real stderr shape)

- Without keyring access: gog exits rc=1 with `no TTY available for keyring file backend password prompt` → classified AUTH_EXPIRED, `events=[]`, note `operator reauthentication required: ...`. Verified live and with a fake reader injection.

## 3. Static checks / full suite

```
ruff check src tests        : All checks passed!
ruff format --check src tests: 252 files already formatted
mypy src/yowayowa           : Success: no issues found in 155 source files
pytest -q (junit)           : tests=633 failures=0 errors=0 skipped=0
```

New tests: mailbox 42 / extraction 26 / service 38 / surfaces 17 = 123.

## 4. Frozen-contract check

`git diff f4b74d4 -- <P1A/P1B/P1C frozen files> .github/` → empty (exit 0).
Frozen paths verified: acquisition/{service,models,registry,parsers,downloads,transport,snapshots,auth,cache,ir,discovery,documents}.py, services/ir_monitor_service.py, operator_bridge/, broker_models.py, .github/workflows.

## 5. Secrets

- Keyring password read from `/root/.config/gogcli/.keyring-password` at runtime only; never printed, logged, or committed.
- No real mail bodies, message ids, or the operator address appear in committed tests (fixtures use synthetic data).
- `.env.example` gains only the two new settings with empty values.
