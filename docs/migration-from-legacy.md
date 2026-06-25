# Migration from legacy (v1 → v2)

`legacy/` is the working v1 monolith, kept **locally as reference only** and
**excluded from version control** (see `legacy/CLAUDE.md` and `.gitignore`).

## ⚠️ Security checklist — do this before legacy ever touches VCS

The legacy code contains **plaintext credentials**:

- `legacy/Code/raw.py` — hardcoded Commodity Essentials `CE_USERNAME` / `CE_PASSWORD`.
- A hardcoded **Kpler API key** in the covariate price/availability fetchers.
- `legacy/Code/.env`.

Required actions:

1. **Rotate** the CE credentials and the Kpler key now — they are compromised the
   moment they exist in source.
2. **Scrub** the literals (replace with `os.environ[...]` reads) before adding any
   legacy file to git.
3. Keep `.env`, `legacy/`, `optuna.db` and data artifacts git-ignored.
4. See ADR 0006.

Until 1–3 are done, do **not** un-ignore `legacy/`.

## Component map

| Legacy | v2 home | Notes |
|---|---|---|
| `main.py` (orchestration) | `etl` CLI + `ml` CLI | Split ingest from forecast; run independently |
| `raw.py`, covariate fetchers (`temps/prices/residual_load/availability/gas_gen`) | **Reference only** → port via `etl/.../connectors/_template/` once v2 sources are chosen; covariate/feature logic → `ml/features/` | Sources are not decided yet (ADR 0003) |
| `models/` (Darts `Model` ABC + impls) | `ml/src/gasbalance_ml/models/` | Keep the `fit`/`predict` abstraction; add a registry |
| `models/scenarios/*` | `ml/src/gasbalance_ml/pipelines/` | Scenario engine |
| Optuna tuning, `optuna.db` | `ml/src/gasbalance_ml/tuning/` | DB git-ignored; experiments tracked in MLflow |
| `settings/*.yaml`, `compile.py` | `etl/settings/` loaded via `core` settings loader | Hierarchical country/region/series config |
| Excel + pickle output | Postgres tables → served by `api/` | No more files-as-interface |
| `ea-connections`, `ea-power-timeseries` | **Not used in v2 (for now)** | v2 connectors talk to sources directly; revisit only if a source genuinely needs them |

## Migration order (suggested)

1. Stand up `core/` (config, db, settings, logging) + Postgres schema in `infra/db`.
2. Pick the first v2 data source; build its connector + Pandera contract + test.
3. Port one model family into `ml/`, wire the registry + a backtest + MLflow.
4. Expose it through `api/`; render it in `web/`.
5. Repeat per source/model. Keep `legacy/` runnable locally for cross-checks until parity is proven.
