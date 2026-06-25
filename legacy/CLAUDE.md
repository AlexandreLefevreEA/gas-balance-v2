# legacy/ — FROZEN v1 (reference only)

This is the original "EU gas balance" pipeline. It is kept **locally** so v2 can be
cross-checked against it during migration.

## Rules

- **Do not edit and do not extend.** v2 supersedes it. New work goes in the v2 subsystems.
- **Not under version control.** `.gitignore` excludes everything here except this file.
- **Contains plaintext secrets** — `Code/raw.py` (CE credentials), the Kpler key in
  the covariate fetchers, and `Code/.env`. See `docs/adr/0006` and
  `docs/migration-from-legacy.md`. Rotate + scrub before this directory ever touches git.

## What it does (orientation)

`Code/main.py` → `Code/raw.py` (pulls ~600 series from the Commodity Essentials API)
→ `Code/models/forecast.py` (per-country/per-series Darts models, Optuna-tuned) →
Excel + pickle. Config is hierarchical YAML under `Code/settings/`.

The legacy → v2 component map lives in `docs/migration-from-legacy.md`.
