"""Kpler long-term power-demand connector — the demand analogue of `kpler_long_term_temperatures`.

Source: `GET /power/loads/forecasts/long-term`, hourly electricity **demand** (total system load,
MW) per power zone — the forward-looking **climatology** counterpart of the day-ahead actuals in
`kpler_power_demand`. Like the long-term temperatures and generation, the flavour is chosen via
`baseWeatherModel`:

- **MEAN** — the "normal" demand profile.
- **REF_YYYY** — the profile if that historical year's weather recurred ("weather years").
  We pull the **last 10 completed years**, recomputed each run (today REF_2016 … REF_2025).

`zones[]` batches every area in one request, so a run is just `len(models)` (= 11) requests,
fanned out concurrently (bounded by `_CONCURRENCY`). One series per (zone x model), code
`KP.LOADLT.<zone>.<MODEL>`; `sub_group` is `demand` so a long-term series lines up with its actual
`KP.LOAD.<zone>` (and forecast `KP.LOADFC.<zone>.<MODEL>`) on the load type. Note this endpoint —
unlike the short-term `/power/loads/forecasts` — takes **no `loadType` param** (it serves total
demand, no demand/residual split), and `zones` is the country-code enum: Germany is **DE**, not
the `DE-LU` bidding zone the generation long-term endpoint wants (verified live; `DE-LU` 422s).

Storage: values are hourly, so they land in the single-vintage `covariate` table (the "actual"
covariate store, same as `kpler_long_term_temperatures` and `kpler_power_demand`) via the `load`
hook — **not** the multi-vintage `forecast_covariate` (ADR 0008).

**Not run-date-independent** (like the long-term generation, unlike the long-term temperatures
whose normals were stable to ≤~0.005 °C): probing showed MEAN can shift by hundreds of MW between
run dates (latest vs 35 days older differed by ~700 MW on a ~42 GW zone). We deliberately **omit
`runDate`** to take the **latest** run and full-refresh weekly into the single-vintage covariate
(idempotent upsert overwrites the prior view). We are not vintaging this — that is what the
`forecast_covariate` store (`kpler_power_demand_forecast`) is for.

Auth: HTTP Basic with `KPLER_API_KEY_V2` (shared with the other Kpler connectors).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import TYPE_CHECKING, Any

import httpx
import pandas as pd

from gasbalance_etl.connectors._kpler_common import last_loaded_at
from gasbalance_etl.connectors._kpler_http import arequest
from gasbalance_etl.connectors.kpler_actual_temps.config import get_kpler_settings
from gasbalance_etl.settings import load_series_dict
from gasbalance_etl.validation.demand import demand_schema

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from gasbalance_etl.connectors.kpler_actual_temps.config import KplerSettings

log = logging.getLogger(__name__)

# --- connector interface (read by the CLI registry) -------------------------
source = "kpler_power_demand_long_term"
schema = demand_schema

_ENDPOINT = "power/loads/forecasts/long-term"
_HORIZON_MONTHS = 24  # forward window pulled each run (matches the other long-term connectors)
_N_REF_YEARS = 10  # number of trailing weather years (REF_YYYY) to pull
_MIN_REFRESH_DAYS = 7  # full refresh is weekly; skip when covariate was loaded more recently.
# ponytail: lower this, or delete the source's covariate rows, to force a refresh sooner.
# sub_group tag only — this endpoint has no demand/residual split (no `loadType` param), so there
# is nothing to send; the tag keeps the long-term series lined up with the actual/forecast demand.
_LOAD_TYPE = "demand"
# ponytail: bound the concurrent in-flight requests. A run is only 11 (model) GETs, but each is a
# big 24-mo x 18-zone payload, so fan them out instead of looping serially while keeping a cap so
# we don't trip the rate limit (the shared helper still retries 429/5xx). Raise if the API
# tolerates more; lower if you see sustained 429s.
_CONCURRENCY = 8


def _models() -> list[str]:
    """MEAN (normal) + REF for the last 10 completed years; auto-advances each year.

    Kpler exposes REF up to REF_{last year}, so REF_{Y-1} is reliably available (verified live:
    REF_2025 returns data, REF_2026 422s "Invalid baseWeatherModel"). Mirrors the other long-term
    connectors' `_models()` — duplicated rather than shared because they are independent sources
    (one interface per connector).
    # ponytail: if Kpler lags publishing REF_{Y-1}, that one model request 422s the run.
    """
    y = dt.date.today().year
    return ["MEAN"] + [f"REF_{yr}" for yr in range(y - _N_REF_YEARS, y)]


def _code(zone: str, model: str) -> str:
    """Series code, e.g. KP.LOADLT.FR.MEAN / KP.LOADLT.FR.REF2020."""
    return f"KP.LOADLT.{zone}.{model.replace('REF_', 'REF')}"


def series_dict() -> list[dict[str, Any]]:
    """Curated dictionary = zone YAML x model list -> one demand series per (zone, model)."""
    zones = load_series_dict(source)  # [{area, zone}, ...]
    return [
        {
            "code": _code(z["zone"], m),
            "name": f"{z['zone']} long-term demand ({m})",
            "group": "demand_longterm",
            "sub_group": _LOAD_TYPE,
            "area": z["area"],
            "unit": "MW",
            "zone": z["zone"],  # used by to_canonical merge + fetch
            "model": m,  # used by to_canonical merge + fetch
        }
        for z in zones
        for m in _models()
    ]


def load(session: Session, df: pd.DataFrame, run_id: int, code_to_id: dict[str, int]) -> int:
    """Route this connector's hourly rows to the single-vintage `covariate` table (ADR 0008).

    Imported lazily so importing the connector (for the registry) stays DB-free.
    """
    from gasbalance_etl.load.upsert import upsert_covariates

    return upsert_covariates(session, df, run_id, code_to_id)


def fetch(since: dt.date | None = None) -> pd.DataFrame:
    """Full refresh: pull the forward [today, today+24mo] hourly demand profile for every model.

    `since` (framework contract) is ignored — we always take the latest run (runDate omitted) and
    re-pull the same forward window; the idempotent upsert overwrites the single-vintage covariate
    in place. One request per model returns all zones (`zones[]`), so a run is `len(models)` (= 11)
    requests, fanned out concurrently (bounded by `_CONCURRENCY`) over the shared 429/5xx retry.
    """
    del since
    last = last_loaded_at(source)
    if last is not None and dt.datetime.now(dt.UTC) - last < dt.timedelta(days=_MIN_REFRESH_DAYS):
        log.info("%s: covariate loaded <%dd ago; skipping full refresh", source, _MIN_REFRESH_DAYS)
        return pd.DataFrame(columns=["zone", "model", "date", "value"])
    cfg = get_kpler_settings()
    entries = series_dict()
    zones = sorted({e["zone"] for e in entries})
    models = _models()
    today = dt.date.today()
    start = today
    end = (pd.Timestamp(today) + pd.DateOffset(months=_HORIZON_MONTHS)).date()
    log.info(
        "kpler-demand-lt: %d models x %d zones, <=%d concurrent (%s..%s)",
        len(models),
        len(zones),
        _CONCURRENCY,
        start,
        end,
    )

    rows = asyncio.run(_fetch_rows(cfg, zones, models, start, end))

    # ponytail: holds the full ~3.5M-row frame in memory (11 models x 18 zones x 24 mo hourly).
    # The CLI loads the whole frame at once; the cheap knob is _HORIZON_MONTHS if it bites.
    df = pd.DataFrame(rows, columns=["zone", "model", "date", "value"])
    if not df.empty:
        # Kpler returns tz-aware UTC ISO; canonical `date` is datetime64[ns] (naive UTC).
        df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
    return df


async def _fetch_rows(
    cfg: KplerSettings,
    zones: list[str],
    models: list[str],
    start: dt.date,
    end: dt.date,
) -> list[tuple[str, str, str, float]]:
    """Fan out one request per model concurrently (bounded), collect raw rows.

    Each request batches all zones (`zones[]`); the response echoes the zone but **not**
    `baseWeatherModel`, so each row is tagged with the model its request asked for. Order is
    irrelevant — `to_canonical` groups. A `_CONCURRENCY` semaphore caps in-flight requests;
    `arequest` handles 429/5xx retries underneath.
    """
    sem = asyncio.Semaphore(_CONCURRENCY)

    async with httpx.AsyncClient(
        base_url=cfg.base_url,
        headers={"Authorization": f"Basic {cfg.api_key_v2}", "Accept": "application/json"},
        timeout=180.0,
    ) as client:

        async def one(m: str) -> list[tuple[str, str, str, float]]:
            async with sem:
                resp = await arequest(
                    client,
                    _ENDPOINT,
                    {
                        "zones": zones,  # all areas in one request (country codes — DE, not DE-LU)
                        "baseWeatherModel": m,  # MEAN / REF_YYYY — not echoed in the response
                        "granularity": "hourly",
                        "timezone": "UTC",
                        "startDate": start.isoformat(),
                        "endDate": end.isoformat(),
                        # runDate omitted -> latest run (NOT run-date-independent; latest is wanted)
                    },
                    label="kpler-demand-lt",
                )
            return [
                (d["zone"], m, d["startDate"], d["value"])
                for d in resp.json().get("data", [])
                if d.get("value") is not None
            ]

        chunks = await asyncio.gather(*(one(m) for m in models))
    return [row for chunk in chunks for row in chunk]


def to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """Map each (zone, model) hourly demand profile to canonical rows via the dictionary."""
    out_cols = ["date", "series_id", "name", "group", "sub_group", "area", "value", "source"]
    meta = pd.DataFrame(
        [
            {
                "zone": e["zone"],
                "model": e["model"],
                "series_id": e["code"],
                "name": e["name"],
                "group": e["group"],
                "sub_group": e["sub_group"],
                "area": e["area"],
            }
            for e in series_dict()
        ]
    )
    df = raw.merge(meta, on=["zone", "model"], how="inner")  # unknown zones/models drop out
    if df.empty:
        return pd.DataFrame(columns=out_cols)
    df["value"] = df["value"].astype(float)
    df["source"] = source
    return df[out_cols]
