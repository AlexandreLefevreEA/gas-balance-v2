"""Kpler temperature-forecast connector — the first **forecast covariate**.

Source: `GET /power/loads/forecasts/temperature` (the base forecast endpoint), hourly
population-weighted 2 m temperature (°C) per power zone. Unlike `kpler_actual_temps`
(which keeps only the D-1 slice as an actual proxy), this keeps the **full forward
horizon** of two 00z models, as a forecast:

- **EC_AIFS_ENS** — the AI (AIFS) ensemble, ~15-day horizon ("AI EC ENS").
- **EC_46** — the 46-day extended forecast.

The response gives the **ensemble mean** (one value per zone/hour) and echoes `model` +
`runDate`. Each balance area x model is one series, code `KP.TEMPFC.<zone>.<MODEL>`;
`sub_group` holds the model.

**The vintage dimension.** A forecast is `(runDate, deliveryDate) → value`: the same
delivery hour appears in every daily run. So values land in `forecast_covariate`, keyed by
`(series_id, made_on, ts)` (`made_on` = the run date), not the single-vintage `covariate`.
See ADR 0009.

**Retention.** Multi-vintage storage is bounded by a rule enforced after every load (and
re-runnable via `etl prune kpler_temps_forecast`): keep **all** runs from the last 15 days,
plus **every Monday** run for 1 year; delete the rest.

**Refresh: self-managing, and backfills missing vintages.** Each run computes the desired
keep-set of run dates (= the retention set), subtracts what's already stored, and fetches
every missing vintage (first run loads the whole set; a daily run loads today's new run; a
run after missed crons fills the gap) plus a small recent overlap re-pulled for revisions.
One request per run date returns both models for all zones. Auth: HTTP Basic with
`KPLER_API_KEY_V2` (shared with `kpler_actual_temps`).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import TYPE_CHECKING, Any

import httpx
import pandas as pd

from gasbalance_etl.connectors._kpler_common import (
    desired_run_dates,
    loaded_run_dates,
    prune_vintages,
    vintages_to_delete,
)
from gasbalance_etl.connectors._kpler_http import arequest
from gasbalance_etl.connectors.kpler_actual_temps.config import get_kpler_settings
from gasbalance_etl.settings import load_series_dict
from gasbalance_etl.validation.forecast_covariate import forecast_covariate_temperature_schema

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from gasbalance_etl.connectors.kpler_actual_temps.config import KplerSettings

log = logging.getLogger(__name__)

# --- connector interface (read by the CLI registry) -------------------------
source = "kpler_temps_forecast"
schema = forecast_covariate_temperature_schema

_ENDPOINT = "power/loads/forecasts/temperature"
# 00z runs of the AI ensemble (~15-day horizon) and the 46-day extended model. Confirmed
# live: both daily, both return an ensemble-mean `value` and echo `model` + `runDate`.
_MODELS = ["EC_AIFS_ENS", "EC_46"]
# Re-pull the most recent few run dates each run, to catch revised (updatedAt) runs.
_REFRESH_DAYS = 3
# Fan the run-dates out concurrently; the global Kpler cap in _kpler_http is the real limiter.
_CONCURRENCY = 8


def _code(zone: str, model: str) -> str:
    """Series code, e.g. KP.TEMPFC.FR.EC_46 / KP.TEMPFC.FR.EC_AIFS_ENS."""
    return f"KP.TEMPFC.{zone}.{model}"


def series_dict() -> list[dict[str, Any]]:
    """Curated dictionary = zone YAML x model list -> one series per (zone, model)."""
    zones = load_series_dict(source)  # [{area, zone}, ...]
    return [
        {
            "code": _code(z["zone"], m),
            "name": f"{z['zone']} temp forecast ({m})",
            "group": "temperature_forecast",
            "sub_group": m,
            "area": z["area"],
            "unit": "degC",
            "zone": z["zone"],  # used by to_canonical merge + fetch
            "model": m,  # used by to_canonical merge + fetch
        }
        for z in zones
        for m in _MODELS
    ]


def _desired_run_dates(today: dt.date) -> list[dt.date]:
    """This connector's fetch keep-set = the shared retention rule (no history floor)."""
    return desired_run_dates(today)


def _vintages_to_delete(made_ons: list[dt.date], today: dt.date) -> set[dt.date]:
    """This connector's retention rule (shared, pure); `prune` wraps it in SQL."""
    return vintages_to_delete(made_ons, today)


def load(session: Session, df: pd.DataFrame, run_id: int, code_to_id: dict[str, int]) -> int:
    """Route hourly forecast rows to `forecast_covariate`, then enforce retention.

    The prune runs in the same transaction (the CLI commits on success), so a failed load
    rolls back both. Imported lazily so importing the connector (for the registry) is DB-free.
    """
    from gasbalance_etl.load.upsert import upsert_forecast_covariates

    written = upsert_forecast_covariates(session, df, run_id, code_to_id)
    prune(session)
    return written


def prune(session: Session) -> int:
    """Delete forecast vintages outside the retention window (shared rule). Returns rows deleted."""
    return prune_vintages(session, source)


async def _fetch_rows(
    cfg: KplerSettings, zones: list[str], run_dates: list[dt.date]
) -> list[tuple[str, str, str, float, str]]:
    """Fetch each run-date concurrently (bounded) -> (zone, model, date, value, runDate) rows."""
    sem = asyncio.Semaphore(_CONCURRENCY)
    async with httpx.AsyncClient(
        base_url=cfg.base_url,
        headers={"Authorization": f"Basic {cfg.api_key_v2}", "Accept": "application/json"},
        timeout=180.0,
    ) as client:

        async def _one(rd: dt.date) -> list[tuple[str, str, str, float, str]]:
            async with sem:
                resp = await arequest(
                    client,
                    _ENDPOINT,
                    {
                        "runDate": rd.isoformat(),
                        "run": "00z",
                        "zones": zones,
                        "models": _MODELS,
                        "granularity": "hourly",
                        "timezone": "UTC",
                    },
                    label="kpler-fc",
                )
            return [
                (d["zone"], d["model"], d["startDate"], d["value"], d["runDate"])
                for d in resp.json().get("data", [])
                if d.get("value") is not None
            ]

        results = await asyncio.gather(*[_one(rd) for rd in run_dates])
    return [row for chunk in results for row in chunk]


def fetch(since: dt.date | None = None) -> pd.DataFrame:
    """Fetch every desired forecast vintage not already stored (+ a recent refresh overlap).

    `since` (framework contract) is ignored — the window is the retention keep-set,
    self-determined from what `forecast_covariate` already holds, so gaps are backfilled.
    """
    del since
    cfg = get_kpler_settings()
    entries = series_dict()
    zones = sorted({e["zone"] for e in entries})
    today = dt.datetime.now(dt.UTC).date()

    desired = set(_desired_run_dates(today))
    have = loaded_run_dates(source)
    refresh = {today - dt.timedelta(days=i) for i in range(_REFRESH_DAYS)}
    # clamp refresh to the keep-set: never re-fetch a vintage that just aged
    # out of the window (only for prune to delete it next run)
    run_dates = sorted((desired - have) | (desired & refresh))

    cols = ["zone", "model", "date", "value", "made_on"]
    if not run_dates:
        return pd.DataFrame(columns=cols)
    log.info(
        "kpler-fc: %d run-dates x %d zones x %d models (today %s)",
        len(run_dates),
        len(zones),
        len(_MODELS),
        today,
    )

    rows = asyncio.run(_fetch_rows(cfg, zones, run_dates))
    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        # Kpler returns tz-aware UTC ISO timestamps; canonical `date` is naive UTC.
        df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
        df["made_on"] = pd.to_datetime(df["made_on"])  # 'YYYY-MM-DD' -> naive datetime64
    return df


def to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """Map each (zone, model) hourly forecast to canonical rows, carrying `made_on`."""
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
    cols = ["date", "series_id", "name", "group", "sub_group", "area", "value", "source", "made_on"]
    return df[cols]
