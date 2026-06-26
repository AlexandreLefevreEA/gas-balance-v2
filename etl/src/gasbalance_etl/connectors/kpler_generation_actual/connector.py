"""Kpler actual power-generation connector — hourly generation by fuel, per zone.

Source: `GET /power/generations/fuel-types`, hourly generation (MW) per power zone and
fuel type. We keep the four fuels relevant to the gas balance — **solar, wind,
run-of-river, gas** — as exogenous covariates for gas-for-power demand. Kpler splits wind
into `wind onshore` + `wind offshore`; we **sum** them into one WIND series. One series per
`(zone, fuel)`, code `KP.GEN.<FUEL>.<zone>`; `sub_group` holds the fuel.

Storage: values are **hourly**, so this loads into the `covariate` table (not the daily
`observation`) via the `load` hook — same as `kpler_actual_temps` (ADR 0008). We store the
raw hourly UTC series; the EU **gas-day (06:00 CET, DST-aware)** aggregation is applied
downstream in `ml/`, not here. (Kpler's own `daily` granularity is calendar-day midnight→
midnight, *not* the gas day — which is exactly why we keep hourly and aggregate later.)

Refresh: **incremental & self-managing**. `fetch()` reads the last loaded `covariate`
timestamp and pulls from there (minus a small overlap for revisions); the first run
backfills from `_HISTORY_START`. The endpoint takes a whole `[startDate, endDate)` window
and returns every zone in one response (no pagination/cap observed), so we fetch in date
chunks (all zones at once) — a handful of sync requests, not one-per-day.

Auth: HTTP Basic with `KPLER_API_KEY_V2` (shared with the other Kpler connectors).
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING, Any

import httpx
import pandas as pd

from gasbalance_etl.connectors._kpler_http import request
from gasbalance_etl.connectors.kpler_actual_temps.config import get_kpler_settings
from gasbalance_etl.settings import load_series_dict
from gasbalance_etl.validation.generation import generation_schema

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# --- connector interface (read by the CLI registry) -------------------------
source = "kpler_generation_actual"
schema = generation_schema

_ENDPOINT = "power/generations/fuel-types"
# Our four fuel series: code -> display name (sub_group = code.lower()).
_FUELS = {"SOLAR": "Solar", "WIND": "Wind", "ROR": "Run-of-river", "GAS": "Gas"}
# Kpler `fuelType` enum -> our fuel code. Wind onshore+offshore fold into one WIND series;
# every other Kpler fuel (nuclear, coal, biomass, …) is simply absent here and dropped.
_KPLER_FUEL_TO_CODE = {
    "solar": "SOLAR",
    "wind onshore": "WIND",
    "wind offshore": "WIND",
    "hydro run-of-river and poundage": "ROR",
    "fossil gas": "GAS",
}
# ENTSO-E-era start; earlier days just return empty and drop out. Widen if Kpler backfills.
_HISTORY_START = dt.date(2015, 1, 1)
# On incremental runs, re-pull this many trailing days to catch late-arriving revisions.
_REFRESH_DAYS = 5
# One response covers a whole window for all zones (~90 d x 18 zones ~ 0.5 M rows, no cap
# seen); chunk the date range so each response stays bounded.
_CHUNK_DAYS = 90


def _code(zone: str, fuel: str) -> str:
    """Series code, e.g. KP.GEN.SOLAR.FR / KP.GEN.WIND.DE."""
    return f"KP.GEN.{fuel}.{zone}"


def series_dict() -> list[dict[str, Any]]:
    """Curated dictionary = zone YAML x fuel list -> one series per (zone, fuel)."""
    zones = load_series_dict(source)  # [{area, zone}, ...]
    return [
        {
            "code": _code(z["zone"], fuel),
            "name": f"{z['zone']} {disp} generation",
            "group": "generation",
            "sub_group": fuel.lower(),
            "area": z["area"],
            "unit": "MW",
            "zone": z["zone"],  # used by to_canonical merge + fetch
            "fuel": fuel,  # used by to_canonical merge
        }
        for z in zones
        for fuel, disp in _FUELS.items()
    ]


def load(session: Session, df: pd.DataFrame, run_id: int, code_to_id: dict[str, int]) -> int:
    """Route this connector's hourly rows to the `covariate` table (ADR 0008).

    Imported lazily so importing the connector (for the registry) stays DB-free.
    """
    from gasbalance_etl.load.upsert import upsert_covariates

    return upsert_covariates(session, df, run_id, code_to_id)


def _last_loaded_ts() -> dt.datetime | None:
    """Latest covariate timestamp already stored for this source (or None)."""
    from sqlalchemy import func, select

    from gasbalance_core.db import SessionLocal
    from gasbalance_core.models import Covariate, Series

    stmt = (
        select(func.max(Covariate.ts))
        .join(Series, Covariate.series_id == Series.id)
        .where(Series.source == source)
    )
    with SessionLocal() as session:
        return session.execute(stmt).scalar_one_or_none()


def _date_chunks(start: dt.date, end: dt.date) -> list[tuple[dt.date, dt.date]]:
    """Split [start, end) into <= _CHUNK_DAYS windows (the API's endDate is exclusive)."""
    chunks: list[tuple[dt.date, dt.date]] = []
    cur = start
    while cur < end:
        nxt = min(cur + dt.timedelta(days=_CHUNK_DAYS), end)
        chunks.append((cur, nxt))
        cur = nxt
    return chunks


def fetch(since: dt.date | None = None) -> pd.DataFrame:
    """Incremental: pull hourly generation for every hour not yet loaded.

    `since` (framework contract) is ignored — the window is self-determined from the
    `covariate` table so a cron run is cheap and the first run backfills history.
    """
    del since
    cfg = get_kpler_settings()
    zones = [e["zone"] for e in load_series_dict(source)]
    last = _last_loaded_ts()
    start = (
        _HISTORY_START
        if last is None
        else max(_HISTORY_START, last.date() - dt.timedelta(days=_REFRESH_DAYS))
    )
    end = dt.datetime.now(dt.UTC).date() + dt.timedelta(days=1)  # endDate exclusive; include today
    cols = ["zone", "fuelType", "date", "value"]
    if start >= end:
        return pd.DataFrame(columns=cols)
    log.info("kpler_generation_actual: %d zones, %s..%s", len(zones), start, end)

    rows: list[dict[str, Any]] = []
    with httpx.Client(
        base_url=cfg.base_url,
        headers={"Authorization": f"Basic {cfg.api_key_v2}", "Accept": "application/json"},
        timeout=180.0,
    ) as client:
        for lo, hi in _date_chunks(start, end):
            resp = request(
                client,
                _ENDPOINT,
                {
                    "zones": zones,
                    "granularity": "hourly",
                    "timezone": "UTC",
                    "startDate": lo.isoformat(),
                    "endDate": hi.isoformat(),
                },
                label="kpler_generation_actual",
            )
            rows.extend(resp.json().get("data", []))

    df = pd.DataFrame(rows, columns=["zone", "fuelType", "startDate", "value"]).rename(
        columns={"startDate": "date"}
    )
    if not df.empty:
        # Kpler returns tz-aware UTC ISO; canonical `date` is datetime64[ns] (naive UTC).
        df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
    return df[cols]


def to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """Map (zone, fuelType) hourly rows to canonical series, folding wind onshore+offshore.

    Drops nulls and any fuel/zone outside the dictionary, then sums the Kpler fuel types
    that map to one of our codes (only WIND has two) per (zone, code, hour).
    """
    out_cols = ["date", "series_id", "name", "group", "sub_group", "area", "value", "source"]
    meta = pd.DataFrame(
        [
            {
                "zone": e["zone"],
                "fuel": e["fuel"],
                "series_id": e["code"],
                "name": e["name"],
                "group": e["group"],
                "sub_group": e["sub_group"],
                "area": e["area"],
            }
            for e in series_dict()
        ]
    )
    df = raw.copy()
    df["fuel"] = df["fuelType"].map(_KPLER_FUEL_TO_CODE)  # unmapped fuels -> NaN
    df = df[df["fuel"].notna() & df["value"].notna()]
    df = df.merge(meta, on=["zone", "fuel"], how="inner")  # unknown zones drop out
    if df.empty:
        return pd.DataFrame(columns=out_cols)
    df["value"] = df["value"].astype(float)
    keys = ["date", "series_id", "name", "group", "sub_group", "area"]
    out = df.groupby(keys, as_index=False, dropna=False)["value"].sum()  # fold wind on+off
    out["source"] = source
    return out[out_cols]
