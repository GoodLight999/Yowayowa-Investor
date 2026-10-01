# Daily strategy signal data-quality contract

The shared service `compute_daily_strategy_signals` powers both
`GET /v1/screening/strategy` and `GET /v1/backtest/signals/daily`, and
`evaluate_strategy_signal_alerts`. Screening failures are isolated per symbol;
invalid persisted closes do not turn the whole HTTP response into a 500.
This contract addresses audit findings F-P4-02, F-P4-03, and F-P4-06.

## Sessions and freshness

- Sort observations by session date; two timestamps on the same date are
  duplicate sessions, not extra daily observations. Reject the symbol rather
  than choosing a duplicate price or deduplicating conflicting sources.
- Build a shared calendar from the union of dates in histories that pass
  session, close, and provenance validation. Weekends and exchange holidays
  are not manufactured as price observations. Rejected future-dated rows do
  not make a healthy member stale.
- Evaluate the latest date on that calendar, not the intersection's last date.
  Members ending earlier are reported as `stale_data` and cannot emit signals
  or alerts. Fresh members continue at the latest date, including when their
  history does not overlap a stale member's history.
- A missing calendar observation inside a member's history within the trailing
  61 calendar sessions is `interior_gaps`; reject that member's signals and
  alerts. Absence before a member's first observation is short history, not
  an interior gap. Gaps older than the active window do not invalidate it.
- No interpolation, forward fill, zero fill, or extending a lookback over
  missing sessions is allowed. The observed-union calendar cannot identify
  dates absent from every member; it is not an authoritative exchange calendar.
  Freshness here is relative to the supplied universe, not to the wall clock.

Low volatility requires 61 closes for 60 daily returns and is ranked by sample
standard deviation, with symbol as the tie breaker and five results maximum.
Mean reversion requires 21 closes for a 20-session endpoint return. A member
with only 21–60 valid contiguous observations can still have a mean-reversion
candidate while `insufficient_60_day_history` describes its unavailable
low-volatility result. A negative return is a research candidate, not a buy
recommendation. An empty usable universe returns `as_of: null` and no signals.

## Fail-closed reasons

`unavailable` holds the first rejection reason for each symbol:

| Reason | Meaning |
| --- | --- |
| `invalid_session` | Missing or unparseable observation date |
| `duplicate_sessions` | Multiple rows map to the same date |
| `invalid_close` | Missing, nonnumeric, boolean, nonfinite, or below `1e-12` close |
| `missing_provenance` | Required source metadata is absent or blank on any row |
| `invalid_provenance` | Invalid HTTP(S) source URL, license class, provider type, or retrieval timestamp without a timezone |
| `mixed_providers` | More than one provider occurs within a symbol's supplied history |
| `stale_data` | Member ends before the latest validated universe date |
| `interior_gaps` | Active observed-session window has an interior missing observation |
| `invalid_returns` | A derived return/volatility is nonfinite or arithmetic fails |
| `insufficient_60_day_history` | Fewer than 61 usable daily closes |

Close and provenance validation cover the full supplied history. Derived
returns and volatility are checked before either metric is published; a
nonfinite derived metric rejects the symbol rather than leaking NaN/Infinity
into rankings or HTTP JSON. Daily routes read all persisted providers, without
filtering away missing source metadata or hiding mixed-provider histories.
The existing personal-only access policy remains unchanged.

## Provenance in alerts

Every source row must carry `provider`, `source_url`, `license_class`, and
`retrieved_at` in addition to its `as_of`. License classes use the existing
domain enum; retrieval timestamps must include a timezone. No source metadata
is invented or replaced with defaults.

The top-level provenance entry retains the latest row's original fields and
an `observations` array covering up to the trailing 61 rows. Each alert carries
its own `provenance` object with the latest source metadata and exactly the 21
source observations used for its 20-session return. Original source URLs,
retrieval timestamps, and as-of values survive even if they vary by row.
The alert evaluator returns that payload unchanged and does not transmit it.

## Regression verification

- `tests/test_strategy_signals.py`: ranks, short histories, freshness semantics.
- `tests/test_strategy_signal_quality.py`: duplicates, interior/old gaps,
  observed weekday calendar, bad closes and overflow, per-row provenance,
  stale-member isolation, alert lineage, both HTTP routes with a real JSONL
  store, and provider filtering regressions.
- `tests/test_backtest_api_cli.py`: production app route and API/CLI wiring.
- External audit: `audit_signal_boundaries.py` from task `t_556421aa` (17 checks).
- Whole repository: `make V=/root/current-work/yowayowa-investor/.venv verify`.

This corrective task is local-only; green local checks are not evidence of
publication, CI, or a production rollout. Independent review and integration
remain separate gates.
