"""Kpler long-term temperature connector.

Source: `GET /power/loads/forecasts/temperature/long-term`, hourly population-weighted
2 m temperature (°C) per power zone — the forward-looking **climatology** used as a demand
covariate, distinct from the day-ahead actuals in `kpler_actual_temps`. Two flavours, both
selected via the `baseWeatherModel` param:

- **MEAN** — the "normal" temperature profile.
- **REF_YYYY** — the profile if that historical year's weather recurred ("weather years").
  We pull the **last 10 completed years** (today REF_2016 … REF_2025), recomputed each run.

So each balance area becomes 11 series (`settings/kpler_long_term_temperatures.yaml` x the
model list). Series codes: `KP.TEMPLT.<zone>.<MODEL>` (e.g. `KP.TEMPLT.FR.MEAN`,
`KP.TEMPLT.FR.REF2020`); `sub_group` holds the raw model value.

Auth: HTTP Basic with `KPLER_API_KEY_V2` (shared with `kpler_actual_temps`).

Refresh: **full, weekly**. The profiles are run-date-independent (verified: MEAN at two
run dates differs by ≤~0.005 °C), so we omit `runDate` (latest run) and pull the forward
window [today, today + 24 months] every run, then upsert idempotently. One request per
model returns all zones (`zones[]`), so a run is just `len(models)` (=11) requests. Values
are hourly, so they land in `covariate` (not the daily `observation`) via the `load` hook.
(See ADR 0008.)
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
from gasbalance_etl.validation.temperature import temperature_schema

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from gasbalance_etl.connectors.kpler_actual_temps.config import KplerSettings

log = logging.getLogger(__name__)

# --- connector interface (read by the CLI registry) -------------------------
source = "kpler_long_term_temperatures"
schema = temperature_schema

_ENDPOINT = "power/loads/forecasts/temperature/long-term"
_HORIZON_MONTHS = 24  # forward window pulled each run (user-chosen)
_N_REF_YEARS = 10  # number of trailing weather years (REF_YYYY) to pull
_MIN_REFRESH_DAYS = 7  # full refresh is weekly; skip when covariate was loaded more recently.
# ponytail: lower this, or delete the source's covariate rows, to force a refresh sooner.
# Fan the models out concurrently; the global Kpler cap in _kpler_http is the real limiter.
_CONCURRENCY = 8


def _models() -> list[str]:
    """MEAN (normal) + REF for the last 10 completed years; auto-advances each year.

    Kpler exposes REF_1999..REF_{last year}, so REF_{Y-1} is reliably available.
    # ponytail: if Kpler lags publishing REF_{Y-1}, that one request 422s the run.
    """
    y = dt.date.today().year
    return ["MEAN"] + [f"REF_{yr}" for yr in range(y - _N_REF_YEARS, y)]


def _code(zone: str, model: str) -> str:
    """Series code, e.g. KP.TEMPLT.FR.MEAN / KP.TEMPLT.FR.REF2020."""
    return f"KP.TEMPLT.{zone}.{model.replace('REF_', 'REF')}"


def series_dict() -> list[dict[str, Any]]:
    """Curated dictionary = zone YAML x model list -> one series per (zone, model)."""
    zones = load_series_dict(source)  # [{area, zone}, ...]
    return [
        {
            "code": _code(z["zone"], m),
            "name": f"{z['zone']} long-term temp ({m})",
            "group": "temperature_longterm",
            "sub_group": m,
            "area": z["area"],
            "unit": "degC",
            "zone": z["zone"],  # used by to_canonical merge + fetch
            "model": m,  # used by to_canonical merge + fetch
        }
        for z in zones
        for m in _models()
    ]


def load(session: Session, df: pd.DataFrame, run_id: int, code_to_id: dict[str, int]) -> int:
    """Route this connector's hourly rows to the `covariate` table (ADR 0008).

    Imported lazily so importing the connector (for the registry) stays DB-free.
    """
    from gasbalance_etl.load.upsert import upsert_covariates

    return upsert_covariates(session, df, run_id, code_to_id)


async def _fetch_rows(
    cfg: KplerSettings, zones: list[str], models: list[str], start: dt.date, end: dt.date
) -> list[tuple[str, str, str, float]]:
    """Fetch each model's profile concurrently (bounded) -> (zone, model, date, value) rows."""
    sem = asyncio.Semaphore(_CONCURRENCY)
    async with httpx.AsyncClient(
        base_url=cfg.base_url,
        headers={"Authorization": f"Basic {cfg.api_key_v2}", "Accept": "application/json"},
        timeout=180.0,
    ) as client:

        async def _one(m: str) -> list[tuple[str, str, str, float]]:
            async with sem:
                resp = await arequest(
                    client,
                    _ENDPOINT,
                    {
                        "zones": zones,
                        "baseWeatherModel": m,  # MEAN / REF_YYYY — not echoed in the response
                        "granularity": "hourly",
                        "timezone": "UTC",
                        "startDate": start.isoformat(),
                        "endDate": end.isoformat(),
                        # runDate omitted -> latest run (profiles are run-date-independent)
                    },
                    label="kpler-lt",
                )
            return [
                (d["zone"], m, d["startDate"], d["value"])
                for d in resp.json().get("data", [])
                if d.get("value") is not None
            ]

        results = await asyncio.gather(*[_one(m) for m in models])
    return [row for chunk in results for row in chunk]


def fetch(since: dt.date | None = None) -> pd.DataFrame:
    """Full refresh: pull the forward [today, today+24mo] hourly profile for every model.

    `since` (framework contract) is ignored — the profiles are run-date-independent, so
    each run re-pulls the same forward window and the idempotent upsert overwrites in place.
    """
    del since
    last = last_loaded_at(source)
    if last is not None and dt.datetime.now(dt.UTC) - last < dt.timedelta(days=_MIN_REFRESH_DAYS):
        log.info("%s: covariate loaded <%dd ago; skipping full refresh", source, _MIN_REFRESH_DAYS)
        return pd.DataFrame(columns=["zone", "model", "date", "value"])
    cfg = get_kpler_settings()
    entries = series_dict()
    zones = sorted({e["zone"] for e in entries})
    models = sorted({e["model"] for e in entries})
    today = dt.date.today()
    start = today
    end = (pd.Timestamp(today) + pd.DateOffset(months=_HORIZON_MONTHS)).date()
    log.info("kpler-lt: %d models x %d zones (%s..%s)", len(models), len(zones), start, end)

    rows = asyncio.run(_fetch_rows(cfg, zones, models, start, end))
    # ponytail: holds the full ~3.5M-row frame in memory; per-model load if it ever bites.
    df = pd.DataFrame(rows, columns=["zone", "model", "date", "value"])
    if not df.empty:
        # Kpler returns tz-aware UTC ISO; canonical `date` is datetime64[ns] (naive UTC).
        df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
    return df


def to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """Map each (zone, model) hourly profile to canonical rows via the dictionary."""
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
    df["source"] = source
    cols = ["date", "series_id", "name", "group", "sub_group", "area", "value", "source"]
    return df[cols]
