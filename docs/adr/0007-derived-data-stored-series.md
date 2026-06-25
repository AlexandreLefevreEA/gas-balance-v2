# 0007. Derived data as stored series, computed by a derived ETL stage

- Status: Accepted
- Date: 2026-06-25

## Context

The product is the EU gas balance and its aggregates (supply, demand, storage,
the supply−demand residual). These are computed from many already-ingested series.
They are **viewable**: the dashboard shows them, the API serves them, and ML may
use them as targets. The scaffold anticipated this — `Series.is_derived` exists, the
`observation` table is shared, `etl/transforms/` was reserved for "derived series",
and the CE connector explicitly deferred cross-column balances. The open question
was *where* derived series are computed and *whether* they are stored.

Legacy computed these the same way (`compute_derived_data.py`: `value = Σ positive −
Σ negative`, aggregated by category), with the identity `supply − demand − storage
withdrawal ≈ 0` (`models/scenarios/balance.py`).

## Decision

Derived data = **stored series**, computed by a **derived stage** that satisfies the
existing connector contract (`source / schema / fetch / to_canonical / series_dict`).
Its `fetch()` reads inputs from Postgres (the v2 series it references, selected by
`group`/`sub_group`) instead of the network; everything else reuses the raw pipeline:
the shared `compose` primitive, `canonical_schema`, the idempotent `observation`
upsert, and the CLI loop. It is registered **last**, so `etl run all` computes derived
series after the raw sources. Results are stored in `observation` with the series
flagged `is_derived=true`. A `check: zero_sum` identity check (opt-in per series)
validates accounting residuals before load.

The dividing line: store a derived series only if someone **views** it or a non-model
consumer **reads** it. Pure **model inputs** (HDD/CDD, lags, calendar, scaling) stay
in `ml/features/`, computed in-memory at fit/predict, never stored.

We do **not** use DB views, dbt, an orchestrator, or new tables.

## Consequences

- Easy: one trusted read path (raw and derived read identically); the validation gate
  (ADR 0002, data-contracts) covers derived data too; almost no new machinery.
- The "no business logic in the API" rule holds — computation stays in ETL.
- Give up: query-time freshness. Derived series are recomputed on each derived run
  (full recompute, idempotent upsert); re-running one raw source alone leaves derived
  stale until the next `etl run all`.
- Group-selection is the one capability added beyond plain code-list composition,
  because the balance aggregates are inherently category sums (the legacy approach).

## Trigger to revisit

- **DB views / hybrid**: if a derived series is purely cosmetic, high-cardinality, and
  genuinely shouldn't be materialised — add it as a view then, not before.
- **Dependency-aware partial recompute**: if full recompute per run gets slow.
- **dbt**: only if a warehouse is adopted (ADR 0002, ADR 0005).
