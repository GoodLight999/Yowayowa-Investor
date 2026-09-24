# P5-A: LLM research brief & ask (morning brief, research Q&A)

Status: implemented (task t_p5a, parent card t_ee4d309e, design fixed
2026-09-25 00:20 JST).

## What this is

Two LLM-driven research surfaces over locally captured evidence:

1. **Morning brief** (`MorningBriefService`, `research-brief` CLI,
   `POST /v1/research/brief`): assembles the day's EDINET flags, credit
   margin surges and macro updates into a deterministic evidence packet and
   asks the configured BYOK provider for a five-section Japanese brief with
   per-claim citations. Persisted one row per run date (delete+insert
   idempotent); optionally delivered through `hermes send --to telegram`.
2. **Research ask** (`research_ask`, `research-ask` CLI,
   `POST /v1/research/ask`): answers one natural-language question from
   deterministic cross-evidence lookups (mentioned 4/5-digit codes against
   persisted screening candidates and the EDINET daily JSONL, plus latest
   macro series), then one strict agent round.

## Provider lane (CTO decision, 2026-09-25)

- Primary lane: **OpenRouter `deepseek/deepseek-chat-v3.1`** via the existing
  OpenAI-compatible BYOK configuration (`YOWAYOWA_OPENAI_COMPATIBLE_*` env
  or per-request `provider` in the API body / `--provider --model --api-key
  --base-url` on the CLI). Native OpenAI `tool_calls` verified by CTO
  measurement. Keys are never hard-coded in code.
- The local `agy`/`gemini` CLI shim (127.0.0.1:8321) flattens messages into
  one prose prompt and returns no `tool_calls`; it is NOT used for these
  agent surfaces (summary-only one-shot lane at most).

## Data sources and invariants

| Input | File/table | Notes |
| --- | --- | --- |
| EDINET daily list | `data/edinet-daily.jsonl` | Keyword classification reuses `classify_edinet_filing` from the P4-D screening pipeline; `docTypeCode=030` (有価証券届出書) rows are additionally flagged 大口提出 (A-3 large-filing input). |
| Credit margin surges | `screening_candidates` table via `read_screening_candidates` | Signals with the `credit_` prefix only; provenance is the persisted capture provenance (PERSONAL_ONLY). |
| Macro updates | `data/macro-observations/{bls,fred,treasury}.jsonl` | Latest value per series with `retrieved_at`; `as_of == run_date` rows are flagged 本日発表 (A-3 macro input). Per-source rows are never merged. |

Financial-correctness invariants (same class as P4-D):

- Missing input is never zero-filled. A missing EDINET file, an empty
  candidates table and missing macro JSONL each render as 「未取得」 in the
  answer and an explicit `coverage.missing_inputs` entry; the brief still
  composes from whatever evidence exists.
- Every citation carries provider / source_url / retrieved_at / as-of, so
  any sentence can be traced to the captured evidence.
- The combined brief inherits the strictest license class of its inputs:
  any credit-margin (PERSONAL_ONLY) evidence makes the brief PERSONAL_ONLY.

## Strict prompt discipline

`compose_brief` and `research_ask` wrap the evidence packet in a prompt that
requires:

- the five fixed sections (`BRIEF_SECTIONS`) in order (brief only);
- no number that is not present in the evidence JSON (捏造禁止) and no
  free-form arithmetic (sums/ratios/comparisons the tools did not compute);
- 「未取得」 marking for every missing input;
- a citation (docID / series_id / source_url / retrieved_at) per claim;
- screening candidates framed as research starting points, never as
  buy/sell recommendations.

The same arithmetic rule was added to the global agent system prompt, and a
new `get_macro_series` AI tool exposes the macro JSONL store to every agent
surface (JSON backend, `series_id` filter, verbatim values only).

## API

All routes are token-gated and fail closed with HTTP 403 outside personal
mode (same enforcement point as `/v1/screening`):

- `POST /v1/research/ask` `{question, provider?}` → `ResearchAskResponse`
  (`answer`, `citations[]`, `tool_trace[]`, `coverage`, provider/model).
- `POST /v1/research/brief` `{run_date?, provider?, persist?}` →
  `{brief: ResearchBrief, persisted}` (default: persist).
- `GET /v1/research/brief[?run_date=]` → latest (or dated) persisted brief.

Note: `GET /v1/research/brief` must be registered before the legacy
`GET /v1/research/{symbol}` route (see `api/app.py` include order), or the
static path is captured by the symbol route — this ordering is load-bearing.

## Persistence

`ResearchBriefRecord` (`research_briefs` table), unique per `run_date`:
regeneration replaces the row atomically (delete + insert in one
transaction, mirroring `persist_screening_run`). `payload` holds the full
`ResearchBrief` JSON minus provenance; `provenance` keeps its own column so
read provenance round-trips exactly.

## CLI

- `yowayowa research-brief [--send] [--run-date YYYY-MM-DD] [--no-persist]`:
  generate + persist; `--send` delivers via `hermes send --to telegram`
  (subprocess list-argv, shell never used, sender injectable for tests).
- `yowayowa research-ask 'QUESTION'`: evidence lookup + one agent round;
  prints answer, citation table, tool trace and missing inputs.
- `yowayowa research-brief-latest`: print the latest persisted brief.

Per-request BYOK overrides: `--provider --model --api-key --base-url`
(`YOWAYOWA_BRIEF_API_KEY` env var supported for the key). The key is passed
to the agent request only and is never echoed or persisted.

## Telegram delivery (A-2)

`MorningBriefService.send_brief(brief, sender=...)` defaults to
`_default_sender` → `["hermes", "send", "--to", "telegram", message]` via
`subprocess.run(check=True, capture_output=True)` — no shell, no quoting
pitfalls. Tests inject a fake sender or monkeypatch `subprocess.run`; the
real delivery is exercised by the CTO end-to-end (one live send).

## Testing

`tests/test_research_brief.py` covers (all offline, fake agent factory):

- macro store: combined-series split (FRED multi-series CSV rows), provenance
  round-trip, missing-file coverage, unparseable-value skip (no zero-fill);
- EDINET summary: classification parity with the screening pipeline, 大口提出
  flag for docTypeCode 030, missing-file coverage;
- macro summary: 本日発表 flag when as_of equals the run date;
- compose: strict prompt contents (5 sections, 捏造禁止, 未取得, evidence
  embedding), coverage for all three families, citation provenance,
  PERSONAL_ONLY inheritance, persistence idempotency;
- send: injected sender, and the default sender's exact `hermes send` argv
  with `shell` never set;
- ask: deterministic tool trace order/content, code matching, evidence
  embedding in the prompt, missing-evidence coverage;
- API: ask/brief round-trip through TestClient, 403 fail-closed outside
  personal mode, OpenAPI paths present;
- CLI: ask and brief through the Typer runner with patched agent.
