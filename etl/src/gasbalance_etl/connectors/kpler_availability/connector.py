"""Kpler plant-availability connector (actual) — daily available capacity by fuel, per country.

Source: `GET /power/outages/availability/fuel-types`, daily **available capacity** (MW) per
country and fuel type. We keep the four thermal fuels relevant to the gas balance — **coal,
gas, lignite, nuclear** — as exogenous covariates for gas-for-power demand: when nuclear/coal
capacity is out, gas fills the gap. One series per `(zone, fuel)`, code `KP.AVAIL.<FUEL>.<zone>`;
`sub_group` holds the fuel.

**Actual = the latest view of the past.** The endpoint takes an optional `asOf` (vintage) param;
**omitting it returns the latest snapshot**, which for past delivery dates is the realized
availability. We therefore omit `asOf` and request delivery dates up to today — the settled
"actual" availability. The forward/vintaged view (how the availability outlook evolved) is the
sibling `kpler_availability_forecast` connector. We store the `central` estimate (the feed also
carries `low`/`high`, populated only for a few major markets — skipped; one-line to add).

Storage: values are **daily** (00:00 UTC), loaded into the `covariate` table via the `load`
hook — same hourly/sub-daily covariate sink as the other Kpler power drivers (ADR 0008). We keep
the raw daily UTC series; any gas-day alignment is applied downstream in `ml/`.

Refresh: **incremental & self-managing**. `fetch()` reads the last loaded `covariate`
timestamp and pulls from there (minus a small overlap for revisions); the first run backfills
from `_HISTORY_START`. `zones` and `fuelTypes` both batch into one request, so we fetch in date
chunks (all zones x all fuels each), fanned out **async** over the shared 429/5xx retry.

Auth: HTTP Basic with `KPLER_API_KEY_V2` (shared with the other Kpler connectors).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import TYPE_CHECKING, Any

import httpx
import pandas as pd

from gasbalance_etl.connectors._kpler_common import date_chunks, last_loaded_ts
from gasbalance_etl.connectors._kpler_http import arequest
from gasbalance_etl.connectors.kpler_actual_temps.config import get_kpler_settings
from gasbalance_etl.settings import load_series_dict
from gasbalance_etl.validation.availability import availability_schema

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from gasbalance_etl.connectors.kpler_actual_temps.config import KplerSettings

log = logging.getLogger(__name__)

# --- connector interface (read by the CLI registry) -------------------------
source = "kpler_availability"
schema = availability_schema

_ENDPOINT = "power/outages/availability/fuel-types"
# Our four fuel series: code -> display name (sub_group = code.lower()).
_FUELS = {"COAL": "Coal", "GAS": "Gas", "LIGNITE": "Lignite", "NUCLEAR": "Nuclear"}
# Kpler `fuelType` enum -> our fuel code. The response echoes `fuelType`, so this also drives the
# request list (we ask for exactly these four). 1:1, no folding (unlike generation's wind on/off).
_KPLER_FUEL_TO_CODE = {
    "fossil hard coal": "COAL",
    "fossil gas": "GAS",
    "fossil brown coal/lignite": "LIGNITE",
    "nuclear": "NUCLEAR",
}
_KPLER_FUELS = list(_KPLER_FUEL_TO_CODE)
# Availability history is deep (FR nuclear back to 2016, probed); earlier days just return empty
# and drop out. Widen if Kpler backfills further.
_HISTORY_START = dt.date(2016, 1, 1)
# On incremental runs, re-pull this many trailing days to catch late-arriving revisions.
_REFRESH_DAYS = 7
# `zones` + `fuelTypes` batch into one response for a whole window (~72 series x 365 d ~ 26k rows,
# no cap seen); chunk the date range so each response stays bounded and the chunks fan out.
_CHUNK_DAYS = 365
# ponytail: bound the concurrent in-flight requests. A full 2016-> backfill is ~11 yearly chunks;
# fan them out instead of looping serially, capped so we don't trip the rate limit (the shared
# helper still retries 429/5xx). Raise if the API tolerates more; lower if you see sustained 429s.
_CONCURRENCY = 8


def _code(zone: str, fuel: str) -> str:
    """Series code, e.g. KP.AVAIL.NUCLEAR.FR / KP.AVAIL.LIGNITE.DE."""
    return f"KP.AVAIL.{fuel}.{zone}"


def series_dict() -> list[dict[str, Any]]:
    """Curated dictionary = zone YAML x fuel list -> one series per (zone, fuel)."""
    zones = load_series_dict(source)  # [{area, zone}, ...]
    return [
        {
            "code": _code(z["zone"], fuel),
            "name": f"{z['zone']} {disp} availability",
            "group": "availability",
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
    """Route this connector's daily rows to the `covariate` table (ADR 0008).

    Imported lazily so importing the connector (for the registry) stays DB-free.
    """
    from gasbalance_etl.load.upsert import upsert_covariates

    return upsert_covariates(session, df, run_id, code_to_id)


def fetch(since: dt.date | None = None) -> pd.DataFrame:
    """Incremental: pull daily availability (latest view, `asOf` omitted) for every day not yet
    loaded.

    `since` (framework contract) is ignored — the window is self-determined from the
    `covariate` table so a cron run is cheap and the first run backfills history.
    """
    del since
    cfg = get_kpler_settings()
    zones = [e["zone"] for e in load_series_dict(source)]
    last = last_loaded_ts(source)
    start = (
        _HISTORY_START
        if last is None
        else max(_HISTORY_START, last.date() - dt.timedelta(days=_REFRESH_DAYS))
    )
    end = dt.datetime.now(dt.UTC).date() + dt.timedelta(days=1)  # endDate exclusive; include today
    cols = ["zone", "fuelType", "date", "value"]
    if start >= end:
        return pd.DataFrame(columns=cols)
    chunks = date_chunks(start, end, _CHUNK_DAYS)
    log.info(
        "kpler_availability: %d zones x %d fuels, %s..%s in %d chunks, <=%d concurrent",
        len(zones),
        len(_KPLER_FUELS),
        start,
        end,
        len(chunks),
        _CONCURRENCY,
    )

    rows = asyncio.run(_fetch_rows(cfg, zones, chunks))

    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        # Kpler returns tz-aware UTC ISO (daily 00:00); canonical `date` is naive UTC.
        df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
    return df


async def _fetch_rows(
    cfg: KplerSettings, zones: list[str], chunks: list[tuple[dt.date, dt.date]]
) -> list[tuple[str, str, str, float]]:
    """Fan out one request per date chunk concurrently (bounded), collect raw rows.

    `zones` + `fuelTypes` batch into a single request per chunk (all areas/fuels at once); the
    response echoes `zone` and `fuelType`. We keep the `central` estimate (drop rows whose
    `central` is null). A `_CONCURRENCY` semaphore caps in-flight requests; `arequest` handles
    429/5xx retries underneath.
    """
    sem = asyncio.Semaphore(_CONCURRENCY)

    async with httpx.AsyncClient(
        base_url=cfg.base_url,
        headers={"Authorization": f"Basic {cfg.api_key_v2}", "Accept": "application/json"},
        timeout=180.0,
    ) as client:

        async def one(lo: dt.date, hi: dt.date) -> list[tuple[str, str, str, float]]:
            async with sem:
                resp = await arequest(
                    client,
                    _ENDPOINT,
                    {
                        "zones": zones,
                        "fuelTypes": _KPLER_FUELS,
                        "granularity": "daily",
                        "timezone": "UTC",
                        "startDate": lo.isoformat(),
                        "endDate": hi.isoformat(),
                    },
                    label="kpler_availability",
                )
            return [
                (d["zone"], d["fuelType"], d["startDate"], d["central"])
                for d in resp.json().get("data", [])
                if d.get("central") is not None
            ]

        out = await asyncio.gather(*(one(lo, hi) for lo, hi in chunks))
    return [row for chunk in out for row in chunk]


def to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """Map (zone, fuelType) daily rows to canonical series.

    Maps the Kpler fuelType to our code (dropping unmapped fuels / nulls), keeps only known
    (zone, fuel) series. Fuels are 1:1 (no folding), so a (zone, fuel, day) is already unique.
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
    df["source"] = source
    return df[out_cols]
