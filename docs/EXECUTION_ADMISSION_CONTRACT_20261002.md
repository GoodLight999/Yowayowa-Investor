# Execution admission / accounting-day contract (2026-10-02)

Scope: local Operator Bridge remediation AS-F2-01-R4 / AS-F2-04. No live-account execution is used to verify this change.

## Live attempts

UTC is the accounting timezone. reserve_order_submission and append_audit acquire BEGIN IMMEDIATE before sampling the timestamp. Each timestamp/day pair comes from one clock sample, not two independent reads. A live order_submit_attempt requires an existing SUBMITTING reservation even if no daily cap is configured. A previously claimed dispatch is not eligible. A cross-day reservation move, capacity check, affected-row check and attempt INSERT share one transaction; an admission error rolls back all changes. A second same-ID attempt adds to the daily count and cannot borrow its already consumed last slot.

Before the first attempt commits, a day change can claim the new day's slot if capacity exists. After an attempt commits, no automatic dispatch-day migration is allowed: any changed day rejects the physical invocation with HTTP409. The committed attempt remains as conservative historical usage; it is not deleted to make the count look equal to actual submissions. UNKNOWN and in-flight reservations remain non-replayable across restart.

## Prepared transport boundary

The connector allocates its RSS ID and constructs arguments before admission. The production XlwingsMacroRunner also resolves the workbook and COM macro callable before admission, using prepare_macro. Simple synthetic/custom MacroRunner implementations without prepare_macro are considered already prepared at run_macro entry; custom runners doing blocking preparation must implement prepare_macro to preserve this boundary.

SQLiteOperatorState.dispatch_submission acquires BEGIN IMMEDIATE, samples a fresh day, verifies current-day SUBMITTING ownership and a current-day committed attempt, checks the daily count, and claims a durable single-use dispatch_at value. A second process or reopened state cannot claim that reservation again. A REJECTED reservation's explicit new reserve operation resets the claim, with the usual capacity checks. A final day check occurs after commit/transaction exit and immediately before the prepared callable. Broker IO does not hold the SQLite writer lock: holding it across a blocked broker prevents same-ID requests from returning their required in-flight409 and would cause lock/timeout regressions. The committed claim survives a transport exception or process crash; outcome remains uncertain and requires reconciliation.

No clock read and Python/COM call can be atomic with arbitrary OS suspension or external broker scheduling. This contract is the last local admission boundary, not a claim about exchange acceptance/settlement date, Excel-side queueing after invocation, clock discontinuities after the final sample, or an indefinitely suspended process between individual instructions. Existing and added deterministic tests cover DB connection/writer waits, connector RSS allocation, Excel workbook/macro resolution and the post-commit clock check. A hard physical broker-day guarantee across those external delays would require a deadline/day fence enforced inside the broker-side transport; that is not provided by this local SQLite change.

## Legacy import

append_audit is not an implicit legacy bypass when max_orders_per_day is None. import_legacy_submit_attempt is the explicit offline backfill API, requires an aware timestamp and nonempty ID, refuses an existing live reservation, converts timestamps to UTC, and migrates imported events into UNKNOWN reservations in the same transaction. Import grants no dispatch authority and no automatic replay. Existing database/schema migration remains supported and preserves legacy rows.

The old audit-count unit fixture now uses this explicit import API without changing its expected events/count. Independent audit oracles (74 original + 8 prior remediation + 9 attempt + 4 migration ownership) remain byte-for-byte unchanged.
