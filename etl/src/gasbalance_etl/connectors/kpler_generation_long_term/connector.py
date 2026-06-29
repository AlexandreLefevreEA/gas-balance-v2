"""Kpler long-term generation connector — the generation analogue of `kpler_long_term_temperatures`.

Source: `GET /power/generations/forecasts/long-term`, hourly **renewable** generation (MW) per
power zone — the forward-looking **climatology** counterpart of `kpler_generation_actual`. Like
the long-term temperatures, the flavour is chosen via `baseWeatherModel`:

- **MEAN** — the "normal" generation profile.
- **REF_YYYY** — the profile if that historical year's weather recurred ("weather years").
  We pull the **last 10 completed years**, recomputed each run (today REF_2016 … REF_2025).

The endpoint's `fuelType` enum is exactly the three **renewables** (it has no gas), each pulled
one per request: **solar, wind, run-of-river** (`hydro run-of-river and poundage`). Unlike the
short-term forecast endpoint, **wind is a single fuel** here (no onshore/offshore split, so no
folding) and **`zones[]` batches every area in one request**. So a run is `fuels x models`
(= 3 x 11 = 33) requests. One series per (zone x fuel x model), code
`KP.GENLT.<FUEL>.<zone>.<MODEL>`; `sub_group` holds the fuel (so a long-term series lines up with
its actual `KP.GEN.<FUEL>.<zone>` on fuel, like the forecast connector).

Storage: values are hourly, so they land in the single-vintage `covariate` table (the "actual"
covariate store, same as `kpler_long_term_temperatures` and `kpler_generation_actual`) via the
`load` hook — **not** the multi-vintage `forecast_covariate` (ADR 0008).

**Not run-date-independent** (this is the key difference from the long-term temperatures, whose
normals were stable to ≤~0.005 °C across run dates). Probing showed the generation profile can
shift by hundreds of MW between two run dates. We deliberately **omit `runDate`** to take the
**latest** run and full-refresh weekly into the single-vintage covariate: each run overwrites the
prior view with the most up-to-date climatology (idempotent upsert). We are not vintaging this —
that is what `kpler_generation_forecast` + `forecast_covariate` are for.

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
from gasbalance_etl.validation.generation import generation_schema

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from gasbalance_etl.connectors.kpler_actual_temps.config import KplerSettings

log = logging.getLogger(__name__)

# --- connector interface (read by the CLI registry) -------------------------
source = "kpler_generation_long_term"
schema = generation_schema

_ENDPOINT = "power/generations/forecasts/long-term"
_HORIZON_MONTHS = 24  # forward window pulled each run (matches kpler_long_term_temperatures)
_N_REF_YEARS = 10  # number of trailing weather years (REF_YYYY) to pull
_MIN_REFRESH_DAYS = 7  # full refresh is weekly; skip when covariate was loaded more recently.
# ponytail: lower this, or delete the source's covariate rows, to force a refresh sooner.
# Our three renewable fuel series: code -> display name (sub_group = code.lower()). Same codes as
# kpler_generation_actual (minus GAS, which the long-term endpoint's fuelType enum doesn't offer).
_FUELS = {"SOLAR": "Solar", "WIND": "Wind", "ROR": "Run-of-river"}
# Our fuel code -> the Kpler `fuelType` query value (singular + required per request). The
# response does NOT echo fuelType, so fetch tags each request's rows with the code it asked for.
# NB: wind is a single fuel here (no onshore/offshore split), unlike the actual/forecast endpoints.
_KPLER_FUELTYPE = {
    "SOLAR": "solar",
    "WIND": "wind",
    "ROR": "hydro run-of-river and poundage",
}
# ponytail: bound the concurrent in-flight requests. A run is only 33 (fuel x model) GETs, but
# each is a big 24-mo x 18-zone payload, so fan them out instead of looping serially while keeping
# a cap so we don't trip the rate limit (the shared helper still retries 429/5xx). Raise if the API
# tolerates more; lower if you see sustained 429s.
_CONCURRENCY = 8


def _models() -> list[str]:
    """MEAN (normal) + REF for the last 10 completed years; auto-advances each year.

    Kpler exposes REF_1999..REF_{last year}, so REF_{Y-1} is reliably available (verified
    REF_2025 live, REF_2026 422s). Mirrors kpler_long_term_temperatures._models() — duplicated
    rather than shared because the two are independent sources (one interface per connector).
    # ponytail: if Kpler lags publishing REF_{Y-1}, that one (fuel, model) request 422s the run.
    """
    y = dt.date.today().year
    return ["MEAN"] + [f"REF_{yr}" for yr in range(y - _N_REF_YEARS, y)]


def _code(zone: str, fuel: str, model: str) -> str:
    """Series code, e.g. KP.GENLT.SOLAR.FR.MEAN / KP.GENLT.WIND.DE-LU.REF2020."""
    return f"KP.GENLT.{fuel}.{zone}.{model.replace('REF_', 'REF')}"


def series_dict() -> list[dict[str, Any]]:
    """Curated dictionary = zone YAML x fuels x models -> one series per (zone, fuel, model)."""
    zones = load_series_dict(source)  # [{area, zone}, ...]
    return [
        {
            "code": _code(z["zone"], fuel, m),
            "name": f"{z['zone']} {disp} long-term generation ({m})",
            "group": "generation_longterm",
            "sub_group": fuel.lower(),
            "area": z["area"],
            "unit": "MW",
            "zone": z["zone"],  # used by to_canonical merge + fetch
            "fuel": fuel,  # fuel CODE; used by to_canonical merge
            "model": m,  # raw model (REF_YYYY); used by to_canonical merge + fetch
        }
        for z in zones
        for fuel, disp in _FUELS.items()
        for m in _models()
    ]


def load(session: Session, df: pd.DataFrame, run_id: int, code_to_id: dict[str, int]) -> int:
    """Route this connector's hourly rows to the single-vintage `covariate` table (ADR 0008).

    Imported lazily so importing the connector (for the registry) stays DB-free.
    """
    from gasbalance_etl.load.upsert import upsert_covariates

    return upsert_covariates(session, df, run_id, code_to_id)


def fetch(since: dt.date | None = None) -> pd.DataFrame:
    """Full refresh: pull the forward [today, today+24mo] hourly profile for every (fuel, model).

    `since` (framework contract) is ignored — we always take the latest run (runDate omitted) and
    re-pull the same forward window; the idempotent upsert overwrites the single-vintage covariate
    in place. One request per (fuel, model) returns all zones (`zones[]`), so a run is
    `len(fuels) x len(models)` (= 3 x 11 = 33) requests, fanned out concurrently (bounded by
    `_CONCURRENCY`) over the shared 429/5xx retry/backoff.
    """
    del since
    last = last_loaded_at(source)
    if last is not None and dt.datetime.now(dt.UTC) - last < dt.timedelta(days=_MIN_REFRESH_DAYS):
        log.info("%s: covariate loaded <%dd ago; skipping full refresh", source, _MIN_REFRESH_DAYS)
        return pd.DataFrame(columns=["zone", "fuel", "model", "date", "value"])
    cfg = get_kpler_settings()
    entries = series_dict()
    zones = sorted({e["zone"] for e in entries})
    models = _models()
    today = dt.date.today()
    start = today
    end = (pd.Timestamp(today) + pd.DateOffset(months=_HORIZON_MONTHS)).date()
    log.info(
        "kpler-gen-lt: %d fuels x %d models x %d zones, <=%d concurrent (%s..%s)",
        len(_FUELS),
        len(models),
        len(zones),
        _CONCURRENCY,
        start,
        end,
    )

    rows = asyncio.run(_fetch_rows(cfg, zones, models, start, end))

    # ponytail: holds the full ~10M-row frame in memory (3x the temp long-term, all 33 requests).
    # The CLI loads the whole canonical frame at once, so streaming would need a CLI change; the
    # cheap knob is _HORIZON_MONTHS if it ever bites.
    df = pd.DataFrame(rows, columns=["zone", "fuel", "model", "date", "value"])
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
) -> list[tuple[str, str, str, str, float]]:
    """Fan out one request per (fuel, model) concurrently (bounded), collect raw rows.

    Each request batches all zones (`zones[]`); the response echoes neither fuelType nor
    baseWeatherModel, so each row is tagged with the (fuel code, model) its request asked for.
    Order is irrelevant — `to_canonical` groups. A `_CONCURRENCY` semaphore caps in-flight
    requests; `arequest` handles 429/5xx retries underneath.
    """
    sem = asyncio.Semaphore(_CONCURRENCY)
    combos = [(fuel, fuel_type, m) for fuel, fuel_type in _KPLER_FUELTYPE.items() for m in models]

    async with httpx.AsyncClient(
        base_url=cfg.base_url,
        headers={"Authorization": f"Basic {cfg.api_key_v2}", "Accept": "application/json"},
        timeout=180.0,
    ) as client:

        async def one(fuel: str, fuel_type: str, m: str) -> list[tuple[str, str, str, str, float]]:
            async with sem:
                resp = await arequest(
                    client,
                    _ENDPOINT,
                    {
                        "zones": zones,  # all areas in one request
                        "fuelType": fuel_type,  # singular + required; not echoed in the response
                        "baseWeatherModel": m,  # MEAN / REF_YYYY — not echoed in the response
                        "granularity": "hourly",
                        "timezone": "UTC",
                        "startDate": start.isoformat(),
                        "endDate": end.isoformat(),
                        # runDate omitted -> latest run (NOT run-date-independent; latest is wanted)
                    },
                    label="kpler-gen-lt",
                )
            return [
                (d["zone"], fuel, m, d["startDate"], d["value"])
                for d in resp.json().get("data", [])
                if d.get("value") is not None
            ]

        chunks = await asyncio.gather(*(one(f, ft, m) for f, ft, m in combos))
    return [row for chunk in chunks for row in chunk]


def to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """Map each (zone, fuel, model) hourly profile to canonical rows via the dictionary.

    `fuel` is already our code (fetch tagged it per request — the response doesn't echo fuelType),
    so the merge on (zone, fuel, model) just attaches series metadata and drops unknown
    zones/fuels/models. No fuel folding (wind is a single fuel on this endpoint).
    """
    out_cols = ["date", "series_id", "name", "group", "sub_group", "area", "value", "source"]
    meta = pd.DataFrame(
        [
            {
                "zone": e["zone"],
                "fuel": e["fuel"],
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
    df = raw.merge(meta, on=["zone", "fuel", "model"], how="inner")  # unknown combos drop out
    if df.empty:
        return pd.DataFrame(columns=out_cols)
    df["value"] = df["value"].astype(float)
    df["source"] = source
    return df[out_cols]
