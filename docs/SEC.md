# SEC EDGAR normalization and strategy enrichment

Yowayowa uses the SEC's official EDGAR XBRL APIs as an official-public source for U.S. issuer fundamentals.

Official API documentation:
https://www.sec.gov/search-filings/edgar-application-programming-interfaces

SEC standard taxonomy index:
https://www.sec.gov/data-research/structured-data/taxonomies-schemas/standard-taxonomies/operating-companies

## Company Facts boundary

The SEC Company Facts API aggregates facts that use non-custom taxonomies such as `us-gaap` and apply to the entire filing entity. It is intentionally not a lossless representation of every dimensional disclosure in an inline XBRL filing.

Consequences for Yowayowa:

- standard entity-wide facts are appropriate for normalized cross-company fundamentals;
- custom issuer extension concepts are not guessed into canonical metrics;
- a value that exists only under an XBRL dimension may not appear as an entity-wide Company Facts value;
- lack of a Company Facts value therefore means “not safely normalized here”, not zero.

The provider preserves period end, optional period start, fiscal year/period, unit, accession, filing date, and form on each normalized point.

## Balance-sheet period consistency

A strategy calculation must not combine the latest value of each metric independently if those values come from different filings or periods.

For SEC Kiyohara-style enrichment, Yowayowa selects current assets and total liabilities only when they share:

- the same period end;
- the same SEC accession;
- USD units;
- instant balance-sheet facts.

The newest common filing is selected. Negative balance values are rejected.

## Noncurrent marketable securities mapping

The Kiyohara formula adds investment securities to current assets. Current marketable securities are already inside current assets, so adding them again would double-count assets.

Yowayowa therefore uses only the direct standard fact:

`us-gaap:MarketableSecuritiesNoncurrent`

as the current exact U.S. analogue for the investment-securities add-on.

The mapping is deliberately narrow:

- `MarketableSecuritiesCurrent` is never added because it is already part of current assets;
- combined current-and-noncurrent concepts are not used because the noncurrent portion cannot be isolated safely;
- equity-only, debt-only, restricted-investment, equity-method, affiliate, and generic other-investment concepts are not summed into the exact value merely to increase coverage;
- dimensional schedules whose balance-sheet location member is `MarketableSecuritiesNoncurrent` are not reconstructed from Company Facts because the API boundary does not preserve arbitrary dimensional facts;
- custom taxonomy concepts are not heuristically matched.

If the direct noncurrent fact is absent for the same filing, the supplement still supplies same-filing current assets and liabilities when available, but leaves investment securities missing. The strategy evaluator then exposes the conservative net-cash ratio as a lower bound and cash-neutral P/E as an upper bound.

## Provenance and source isolation

Provider supplements are atomic accounting scopes.

Once a provider supplement is selected, Yowayowa does not combine its current assets/liabilities with a candidate-level investment-securities number from another source. Either the provider supplies a compatible investment-securities value from the same accounting scope or the result remains a bound.

This applies symmetrically to EDINET and SEC enrichment.

## Future expansion

Coverage may be expanded only with evidence-backed mappings whose semantics are sufficiently close to the intended balance-sheet category.

Before adding a new US-GAAP concept:

1. inspect the official taxonomy definition and references;
2. confirm whether it is current, noncurrent, or combined;
3. determine whether it overlaps an already normalized asset category;
4. inspect real issuer filings and Company Facts behavior;
5. decide whether the concept is an exact alternative, a lower-bound component, or unusable;
6. add explicit tests for overlap, period/accession mismatch, units, amendments, and absence.

Do not build a “best effort” sum of vaguely investment-like concepts.
