"""Postgres readers for forecasting — the one DB-bound module (everything else is pure).

Reads the target (`observation`), daily drivers (`covariate` / `forecast_covariate`), and
the small dictionary lookups the orchestration needs (the demand universe, the weather-
scenario model tokens). This module is **read-only**; forecast writes live in `publish.py`.
`core.db`/`core.models` are imported INSIDE the methods so importing this module never builds
the engine — the pure tests stay DB-free (same convention as etl/transforms/derived.py).

Driver reads collapse hourly source data to a daily mean (the grain the features use):
  - `read_daily_actual` — realized `covariate` (perfect-foresight driver; the running layer
    also feeds a REF series through this path for a weather scenario),
  - `read_daily_vintage` — `forecast_covariate`, the latest vintage `made_on <= origin` per
    delivery hour (point-in-time; the leakage-free audit driver).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pandas as pd
from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _hourly_to_daily(rows: list[Any]) -> pd.Series:
    """(ts, value) rows -> daily-mean Series indexed by tz-naive midnight."""
    if not rows:
        return pd.Series(dtype=float)
    idx = pd.DatetimeIndex([pd.Timestamp(t) for t, _ in rows])
    s = pd.Series([float(v) for _, v in rows], index=idx, dtype=float)
    if s.index.tz is not None:
        s.index = s.index.tz_convert("UTC").tz_localize(None)
    return s.groupby(s.index.normalize()).mean()


class PostgresData:
    """Thin read-only DB accessor; one short-lived session per call (lazy core imports)."""

    def _series_id(self, session: Session, code: str) -> int:
        from gasbalance_core.models import Series

        sid = session.execute(select(Series.id).where(Series.code == code)).scalar_one_or_none()
        if sid is None:
            raise KeyError(f"series '{code}' not found in the dictionary")
        return int(sid)

    def read_target(self, code: str) -> pd.Series:
        from gasbalance_core.db import SessionLocal
        from gasbalance_core.models import Observation

        with SessionLocal() as session:
            sid = self._series_id(session, code)
            rows = session.execute(
                select(Observation.obs_date, Observation.value)
                .where(Observation.series_id == sid)
                .order_by(Observation.obs_date)
            ).all()
        if not rows:
            return pd.Series(dtype=float)
        idx = pd.DatetimeIndex([pd.Timestamp(d) for d, _ in rows])
        return pd.Series([float(v) for _, v in rows], index=idx, dtype=float)

    def read_daily_actual(self, code: str) -> pd.Series:
        from gasbalance_core.db import SessionLocal
        from gasbalance_core.models import Covariate

        with SessionLocal() as session:
            sid = self._series_id(session, code)
            rows = session.execute(
                select(Covariate.ts, Covariate.value)
                .where(Covariate.series_id == sid)
                .order_by(Covariate.ts)
            ).all()
        return _hourly_to_daily(list(rows))

    def read_daily_vintage(self, code: str, origin: pd.Timestamp) -> pd.Series:
        from gasbalance_core.db import SessionLocal
        from gasbalance_core.models import ForecastCovariate

        cutoff = pd.Timestamp(origin).date()
        with SessionLocal() as session:
            sid = self._series_id(session, code)
            # DISTINCT ON (ts) + ORDER BY ts, made_on DESC = newest vintage <= cutoff per hour.
            rows = session.execute(
                select(ForecastCovariate.ts, ForecastCovariate.value)
                .where(ForecastCovariate.series_id == sid, ForecastCovariate.made_on <= cutoff)
                .order_by(ForecastCovariate.ts, ForecastCovariate.made_on.desc())
                .distinct(ForecastCovariate.ts)
            ).all()
        return _hourly_to_daily(list(rows))

    def read_demand_universe(self) -> list[tuple[str, str]]:
        """`(code, area)` for the temperature-driven demand series we forecast.

        Structural filter on the dictionary (NOT name matching): `category='demand'`,
        `sub_group` in the temp-driven set (LDZ/IND/total — GTP is power-driven, deferred),
        active and not a derived aggregate. `area` is the zone code that keys the
        temperature drivers (`KP.TEMP.<area>`, `KP.TEMPLT.<area>.<MODEL>`).
        """
        from gasbalance_core.db import SessionLocal
        from gasbalance_core.models import Series

        with SessionLocal() as session:
            rows = session.execute(
                select(Series.code, Series.area)
                .where(
                    Series.category == "demand",
                    Series.sub_group.in_(("LDZ", "IND", "total")),
                    Series.is_active.is_(True),
                    Series.is_derived.is_(False),
                    Series.area.is_not(None),
                )
                .order_by(Series.code)
            ).all()
        return [(str(code), str(area)) for code, area in rows]

    def read_scenario_models(self) -> list[str]:
        """The weather-scenario MODEL tokens present in the long-term temp dictionary.

        `KP.TEMPLT.<zone>.<MODEL>` -> distinct `MODEL` (`MEAN`, `REF_2016`..). Discovered
        from the dictionary, not hardcoded, so the scenario set rolls forward with the
        ref years. These are the `scenario.code` values the forecast run writes against.
        """
        from gasbalance_core.db import SessionLocal
        from gasbalance_core.models import Series

        with SessionLocal() as session:
            rows = session.execute(select(Series.code).where(Series.code.like("KP.TEMPLT.%"))).all()
        return sorted({str(code).rsplit(".", 1)[-1] for (code,) in rows})
