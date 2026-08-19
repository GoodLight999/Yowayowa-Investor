# Yowayowa-Investor Data License Policy

Reviewed: 2026-08-18

This document records the product's engineering policy for data-source rights. It is not a substitute for provider contracts or legal advice. Runtime enforcement lives in `services/licensing.py`; documentation must not claim broader rights than that registry permits.

## Core rule

Public mode is **fail closed**. A source is not publicly displayable or redistributable merely because it is reachable on the web, has an API, is free of charge, or uses a user-supplied API key.

The product separately records:

- access method;
- commercial-use permission;
- public-display permission;
- public-API / redistribution permission;
- permission to publish Yowayowa-derived analysis;
- required attribution;
- review date and governing terms URL.

An unknown source is denied in public mode until it is deliberately classified.

## Current source matrix

| Source | Public display/API | Derived public analysis | Operational policy |
|---|---|---|---|
| SEC EDGAR | Yes | Yes | Official public source. Respect SEC fair-access limits and automated-client identification. Do not reuse SEC marks as product branding. |
| EDINET | Yes, subject to PDL1.0/site terms | Yes | Use API where it provides the content. Attribute EDINET and identify Yowayowa processing. Taxonomy, marks and specifically excluded assets are outside the general content permission. |
| U.S. Bureau of Labor Statistics | Yes | Yes | Published BLS material is treated as public domain except identified third-party assets. Cite BLS, retrieval time where relevant, and do not imply BLS endorsement of downstream analysis. |
| U.S. Bureau of Economic Analysis | Yes | Yes | BEA-published data are treated as public domain except content explicitly identified as third-party copyrighted material. API access uses a free registered key; cite BEA and do not imply BEA/Commerce endorsement of downstream analysis. |
| U.S. Treasury rate data | Yes | Yes | Public product uses the official rate feed; unrelated site assets/marks are outside the policy. |
| FRED generic catalog/series | No | No by default | Personal/BYOK only. FRED aggregates series whose original providers can impose different copyright terms. A FRED API key is not a redistribution license. A future series-specific allowlist may promote individually verified series. |
| Yahoo Finance / yfinance | No | No | Personal mode only. Never use as a public redistribution source. |

## Public research architecture

Anonymous public research routes are a narrow allowlist. Today this includes license-clear SEC fundamentals, SEC-derived screening/comparison, BLS and BEA macro data, Treasury yield curves, SEC 13F research, EDINET document metadata/data and the source-license catalog.

Public browser navigation only exposes surfaces that can operate on those approved sources. Personal-only or contract-pending surfaces redirect to the public Macro workspace rather than presenting controls that can only fail or invoke restricted sources.

Private workspace state remains authenticated: Watchlists, Portfolios, saved Research Presets, Alerts, Event Inbox and other owner data are not made anonymous merely because the deployment is public.

Restricted market/news/calendar providers remain blocked even when an administrator supplies the API token. Public mode cannot use the legacy `allow_personal_provider_in_public` escape hatch.

## Credentials are not rights

`USER_KEY` / registered API access describes how data is obtained, not what Yowayowa may redistribute. EDINET and BEA illustrate one direction: each can require registered API access while the covered public data can still be reusable under its governing terms. FRED illustrates the opposite: an API key provides access but does not clear every underlying series for redistribution.

## Personal local enrichment

The optional enrichment boundary exists to preserve a powerful private research experience without contaminating the public product.

- only `mode=personal` may set `YOWAYOWA_LOCAL_ENRICHMENT_ENABLED=true`;
- public mode rejects that configuration at startup;
- enrichment adapters must be source-specific rather than a generic arbitrary-URL proxy;
- adapters must retain source URL/provider/time and a restrictive license classification;
- private enrichment must not be exposed through anonymous public API routes;
- private enrichment must not silently become a public persisted cache;
- adapters must not bypass authentication, paywalls, CAPTCHAs, access controls or other technical restrictions;
- prefer official APIs/feeds when they provide the required information;
- any page retrieval adapter must be reviewed against the source's current terms and automated-access rules before it is enabled.

This boundary is intentionally compatible with locally executed personal research adapters while preventing those adapters from becoming a covert public redistribution service.

## Public market-price roadmap

Real-time/global market prices are the largest remaining external licensing dependency. Before a market provider can replace the personal-only Yahoo implementation in public mode, its contract must explicitly cover Yowayowa's actual product behavior, including external display and—where JSON/API values are exposed—redistribution/API rights rather than display-only rights.

Candidates should be integrated through a provider adapter only after the contractual rights are represented in the runtime source registry. For Japanese market data, individual J-Quants access is not treated as public redistribution permission; use an appropriate JPX commercial/external-distribution arrangement where needed.

## Review discipline

- Re-review source terms before public launch and after material provider-policy changes.
- Record the review date in the runtime registry.
- Add tests for every newly public-approved source.
- Do not deploy a public head if an unclassified/restricted provider can leak through a public route.
- Derived metrics inherit the restrictions of every source used to calculate them; mixing a restricted price feed into an otherwise public SEC calculation does not make the result publicly safe.
