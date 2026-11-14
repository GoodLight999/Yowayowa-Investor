# Broker acceptance matrix — Rakuten Securities Web

Updated: 2026-09-26 (CG-20260925-004)

This matrix is the single source of truth for broker acceptance states.
It deliberately separates **fixture-green** (automated tests),
**code-complete** (implemented but the live step has never run), and
**real-session verified** (confirmed against the operator's real
authenticated Rakuten session). A connector is *operationally* complete
only when its rows reach real-session states — fixture-green alone is
never "implemented".

Status vocabulary:

- `fixture-green` — proven by automated tests with recorded fixtures;
- `code-complete` — implemented and unit-tested, live step unproven
  (selectors/URLs are explicit assumptions, `verified=False`);
- `real-read-green` — confirmed on the real authenticated session (GET only);
- `real-write-green` — confirmed on the real authenticated session
  (state-changing, with audit-trail evidence);
- `blocked` — requires operator action that has not happened.

## Read side (P1B — `broker-read`)

| # | Row | Status | Evidence / gate |
|---|---|---|---|
| R1 | JP account read | `fixture-green` | `tests/test_broker_read_*.py`; live URLs `verified=False` |
| R2 | US account read | `fixture-green` | same catalog; live URLs `verified=False` |
| R3 | JP positions | `fixture-green` | parser fixtures; live unconfirmed |
| R4 | US positions | `fixture-green` | parser fixtures; live unconfirmed |
| R5 | open orders | `fixture-green` | parser fixtures; live unconfirmed |
| R6 | order history | `fixture-green` | parser fixtures; live unconfirmed |
| R7 | executions / fills | `fixture-green` | parser fixtures; live unconfirmed |
| R8 | auth probe / reauth detection | `fixture-green` | marker-based detection tested; real MFA flow unconfirmed |

**Read-side unlock procedure:** first real operator session follows
`docs/RAKUTEN_WEB_SESSION.md` — confirm each catalog URL in devtools,
flip `verified=True`, re-run R1–R8, record the session date here.

## Write side (P2 — `broker-execution`)

| # | Row | Status | Evidence / gate |
|---|---|---|---|
| W1 | order proposal + preview (no network) | `fixture-green` | `tests/test_broker_execution_domain.py` |
| W2 | interlocks / audit trail / replay | `fixture-green` | 29-test domain suite + tamper/replay cases |
| W3 | submission transport (JP) | `code-complete` | `RakutenWebSubmissionTransport`; order-form selectors are **unverified assumptions**; master gate `submissions_enabled=False` |
| W4 | submission transport (US) | `code-complete` | same transport, `market_codes.us` mapping unproven live |
| W5 | order status inquiry (audit-matched) | `fixture-green` | `tests/test_order_inquiry.py`; reads via R5/R6 so inherits their URL risk |
| W6 | portfolio reflection after fill | `blocked` | needs R3 + a real fill to reconcile |
| W7 | cancel (P2C2) | `blocked` (design frozen, NOT implemented) | implemented only after `cancels_enabled` gate + real-session evidence per `docs/OPERATOR_MODE.md` |
| W8 | fill reconciliation | `blocked` | depends on R7 real-read + a real submission |

## Standing safety gates (never relaxed for convenience)

1. Live submission stays **fail-closed**: master gate `submissions_enabled`
   (default False) AND explicit runtime arming are both required; while
   shut, every submit is REJECTED before any session access and audited
   as `state(submit-frozen)`.
2. `accepted=True` requires a broker order number read back from the
   confirmation page; DOM errors return UNKNOWN and are audited.
3. Cancellation is deliberately non-idempotent and never auto-replayed.
4. No HTTP route exists for submit or cancel — the audit chain and
   failure containment live in the web-session transport only.
5. Single-writer audit directory (M4): the transport is the only writer
   of `stage=submit` / `stage=cancel` entries.

## How to update this matrix

After each real operator session: update the row status, add the date and
a one-line evidence pointer (audit snapshot id / broker order id prefix /
screenshot path in the session doc — never secrets), and re-run the
relevant reconciliation checklist in `docs/RAKUTEN_WEB_SESSION.md`.
