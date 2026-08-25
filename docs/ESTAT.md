# e-Stat official statistics research

Yowayowa-Investor uses the Government of Japan e-Stat REST API as a public-safe original source for Japanese macroeconomic and statistical research.

## Why discovery comes before table IDs

The normal path is:

`meaningful search -> official statistics table -> metadata/dimensions -> bounded fact request`

Yowayowa deliberately does not make a fixed Consumer Price Index table ID the primary contract. e-Stat can replace statistics table IDs during base revisions and other publication changes. The July 2026 CPI base-revision notice is a concrete example. Search and metadata therefore remain first-class product operations, while table IDs are treated as source identifiers discovered from e-Stat rather than durable Yowayowa concepts.

## Server configuration

API access requires an e-Stat application ID:

```bash
YOWAYOWA_ESTAT_APP_ID=...
```

The credential stays server-side. Public browser/API clients never receive it.

## API

- `GET /v1/macro/estat/tables?q=...&lang=J|E&limit=...`
  - searches official statistics tables through e-Stat `getStatsList`.
- `GET /v1/macro/estat/{stats_data_id}/meta?lang=J|E`
  - retrieves the table metadata and official dimension/code catalog through `getMetaInfo`.
- `GET /v1/macro/estat/{stats_data_id}/data`
  - retrieves official facts through `getStatsData`.
  - repeat `filter=` with known dimensions, for example `filter=area=00000&filter=cat01=0001,0002`.
  - supported dimension aliases are `tab`, `time`, `area`, `cat01` through `cat15` (and their canonical `cd_*` forms).
  - response size is bounded to at most 10,000 values per request; `next_key` / `start_position` expose source pagination rather than silently truncating coverage.

Unknown filter names are rejected instead of being forwarded as arbitrary upstream query parameters.

## Data contract

Each fact preserves:

- the exact e-Stat source string as `value`;
- `numeric_value` only when the source string can be represented safely as a decimal number;
- source unit and annotation code;
- all returned dimension/code pairs.

The application does not round the source string for display. Missing/suppressed/non-numeric source markers remain visible and produce `numeric_value = null` rather than a fabricated number.

Metadata retains the official dimension IDs, labels, item codes, names, hierarchy levels, parent codes and units. This lets later curated CPI, labor, wage or industrial-production views reuse the same generic source contract instead of duplicating table-specific parsers.

## CLI

```bash
yowayowa macro estat-search "消費者物価指数"
yowayowa macro estat-meta 0003427113
yowayowa macro estat 0003427113 -f area=00000 -f cat01=0001,0002
```

Long dimension catalogs are bounded in CLI display with `--max-items`. Data requests support the same repeatable filters and source pagination exposed by REST.

## Browser

The `/macro` page includes an e-Stat workbench:

1. search official Japanese statistics by meaning;
2. choose a returned table;
3. inspect its current official dimensions and codes;
4. enter one or more codes per dimension;
5. retrieve and inspect exact source facts and provenance.

Japanese UI requests `lang=J`; English UI requests `lang=E`.

## Provenance and licensing

The executable licensing registry classifies `estat` as `official_public` with registered-key access and public display/API/derived-analysis rights. Public responses retain e-Stat provenance.

Published Yowayowa surfaces credit the Portal Site of Official Statistics of Japan (e-Stat), and Yowayowa identifies its own processing separately. Third-party content and marks excluded by e-Stat's terms are not treated as generally reusable data.

## Validation

Unit fixtures model official `getStatsList`, `getMetaInfo` and `getStatsData` JSON shapes and cover:

- table discovery;
- metadata/dimension preservation;
- exact source values plus Decimal normalization;
- notes and annotations;
- caching;
- filter allowlisting and limits;
- registered App ID handling without credential leakage.

Playwright covers the browser flow from table search through metadata/dimension filtering to exact fact rendering and confirms locale-aware `J` / `E` API selection.
