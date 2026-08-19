# Public original-source analysis stack

Yowayowa-Investor public mode prefers the original statistical or disclosure authority over convenience aggregators whenever practical.

Current public-safe source families:

- SEC EDGAR — U.S. issuer fundamentals, filings and institutional 13F research.
- EDINET — Japanese disclosure content covered by the applicable EDINET Public Data License/site terms.
- U.S. Bureau of Labor Statistics — CPI, labor-market and wage series.
- U.S. Bureau of Economic Analysis — NIPA tables including GDP and consumption analysis.
- U.S. Treasury — official Treasury yield-curve/rate data.

Restricted convenience sources remain outside anonymous public research:

- Generic FRED is personal/BYOK until each underlying series is rights-cleared.
- Yahoo Finance / yfinance is personal-only.

The executable policy is `src/yowayowa/services/licensing.py`; `LICENSE_POLICY.md` records the rationale and source terms. Public mode is fail-closed, so a source that is absent from the registry cannot silently become publicly redistributable data.
