# 0003. Source-agnostic ETL: connector contract + template, sources TBD

- Status: Accepted
- Date: 2026-06-25

## Context

A core goal of v2 is to **change data sources**. The v1 sources (Commodity
Essentials, Kpler, ENTSOE, Meteologica, internal Postgres) are not committed to for
v2 — the owner chose "none for now". Hardcoding a connector list would bake in the
very thing we want to be free to swap.

## Decision

Build the ETL **source-agnostic**. The scaffold ships:

- a **connector contract** (one interface every source implements: `fetch()` →
  `to_canonical()`), documented in `etl/src/gasbalance_etl/connectors/CLAUDE.md`;
- a **`_template/`** to copy when adding a source;

…and **zero pre-built connectors**. Each future source is a self-contained
subpackage, independently runnable and validated, added via `/add-connector`.

## Consequences

- Easy: add/replace/run a source in isolation; no source is special.
- Easy: every source is forced through the same validation contract.
- Give up: nothing concrete to run on day one — the first real value needs a chosen source.

## Trigger to revisit

n/a — adding sources is the steady state. Revisit the *contract* only if a source
can't be expressed as fetch→canonical (e.g. streaming/event data).
