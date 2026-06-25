# Data contracts — what "trusted data" means here

Trust is enforced, not assumed. Nothing reaches the trusted store unless it passes
its contract. A contract is a schema + a set of checks that data must satisfy
**before load** (ETL) and **at the API boundary** (serving).

## Where validation runs

| Layer | Tool | Checks |
|---|---|---|
| ETL, per connector, pre-load | **Pandera** schema in `etl/src/gasbalance_etl/validation/` | columns present, dtypes, value ranges, nullability, timestamp monotonicity, no duplicate `(date, series)` |
| ETL, cross-cutting | small Python checks | freshness (latest date within tolerance), row-count sanity, balance identities hold |
| API boundary | **Pydantic v2** request/response models | shape & types of what goes in/out |

> One tool per job, no overlap: Pandera for dataframes, Pydantic for the API edge.
> dbt/Great Expectations are deliberately deferred (ADR 0005) — add only if a
> warehouse appears.

## The canonical series schema

Every connector must map its raw data to this shape before it can be loaded
(exact column names finalised with the first connector):

| Column | Type | Notes |
|---|---|---|
| `date` | date | daily granularity |
| `series_id` | str | stable id from the settings config |
| `name` | str | human label |
| `group` / `sub_group` | str | demand / supply / flow / storage … |
| `area` | str | country / zone |
| `value` | float | in canonical units (mcm) |
| `source` | str | which connector produced it |
| `loaded_at` | timestamp | when it landed |

## A connector's contract checklist

- [ ] Output matches the canonical schema (Pandera passes).
- [ ] Ranges are sane (no negative storage, flows within capacity, etc.).
- [ ] No duplicate `(date, series_id)`.
- [ ] Freshness within the source's expected lag.
- [ ] A contract test exists against a recorded fixture (no live network in CI).

## On failure

Validation failure **blocks the load** and surfaces loudly (non-zero exit, logged
diff of offending rows). Partial/garbage data is never written. This is the single
most important property of v2 vs the legacy ad-hoc `fillna`/duplicate checks.
