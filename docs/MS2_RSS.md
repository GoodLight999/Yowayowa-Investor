# MS2 RSS (MARKET SPEED II) batch quote pipeline

Personal-only route: quotes come from the operator's own Rakuten Securities
account feed through MARKET SPEED II RSS on the Windows Node. The VPS never
talks to Windows — data flows one way:

```
[Windows Node]                                    [VPS (yowayowa-investor)]
Excel + MARKET SPEED II RSS
  └─ scripts/windows/ms2_rss_export.py   scp   data/ms2/quotes/ms2_quotes_YYYYMMDD.jsonl
        F:\yowayowa\ms2\ms2_quotes_YYYYMMDD.jsonl  ────►  src/yowayowa/providers/ms2_rss_file.py
```

License class: **PERSONAL_ONLY** (`SourceLicensePolicy(key="rakuten-ms2-rss")`
in `src/yowayowa/services/licensing.py`). The VPS side is a receiver only — no
scheduler lives here.

## 1. Windows Node: one-shot batch export

Prerequisites (operator machine only):

- MARKET SPEED II is installed, launched, and logged in (RSS functions need
  the login session).
- Windows Python 3.11+ with `pip install pywin32`.
- Staging directory `F:\yowayowa\ms2\` exists (the exporter creates it if
  missing).

Run (from `cmd.exe` or PowerShell):

```bat
cd \path\to\checkout\scripts\windows
python ms2_rss_export.py --symbols 7203 --name 7203=トヨタ自動車
```

- `--symbols` is the comma/space-separated instrument code list. Start with
  just the one working instrument that today's RSS sheet already renders —
  the default acceptance target.
- Output file: `F:\yowayowa\ms2\ms2_quotes_YYYYMMDD.jsonl` (date = JST),
  appended, one record per line:

```json
{"symbol": "7203", "name": "トヨタ自動車", "quotes": {"bid": 2857.0, "ask": 2858.5, "high": 2872.0, "low": 2841.0, "last": 2857.5, "volume": 27543100.0}, "as_of": "2026-09-24T15:05:32+09:00", "retrieved_at": "2026-09-24T06:05:32+00:00"}
```

Any of the six four-price fields that MS2 could not fill must be exported as
`null` — never `0`.

## 2. Windows Node: scheduled run (operator environment work)

Task Scheduler → Create Task → trigger daily 16:15 JST (after close),
action `python.exe` args `ms2_rss_export.py --symbols 7203 --name
7203=トヨタ自動車`, working dir as in §1. Every other route (push from the
VPS, SSH into Windows) is out of scope by design.

## 3. Copy to VPS (SCP)

From the Windows Node (OpenSSH client, key auth to the VPS):

```bat
scp F:\yowayowa\ms2\ms2_quotes_%date:~0,4%%date:~5,2%%date:~8,2%.jsonl vps:/path/to/yowayowa-investor/data/ms2/quotes/
```

(or copy manually with any SFTP client — the VPS receiver re-reads the whole
directory each time, so file arrival order does not matter.)

## 4. VPS: receive / verify

```bash
mkdir -p data/ms2/quotes && ls data/ms2/quotes/*.jsonl
python -c "from yowayowa.providers.ms2_rss_file import Ms2RssFileProvider; from yowayowa.config import get_settings; print(Ms2RssFileProvider(get_settings()).quotes(['7203']))"
```

Expected: a `MarketQuoteBatch` with `7203`, matching `price` (= `last`),
`as_of` from the JSONL, `unavailable_symbols=[]`. Missing-file /
missing-symbol behavior: `unavailable_symbols=['7203']` (batch) or
`Ms2RssLookupError` (single `quote()`); URL-less store with an unreadable
file raises `Ms2RssFileError` instead of silently hiding the failure.
