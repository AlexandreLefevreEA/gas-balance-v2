"""Forecast-error metrics, computed on the fly (D2): `forecast` vintages joined to `observation`.

Single source of truth — no metrics table. Uses ALL vintages (each made_on is a different
horizon for the same target_date), inner-joined to actuals so only realized errors count.
Skill is left to the client (1 - mae[model]/mae[seasonal_naive]) from the per-model MAEs here.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from gasbalance_api.schemas.metrics import MetricBucket, MetricGroup
from gasbalance_core.models import Forecast, Observation, Series

# Horizon-day buckets — mirror ml/evaluation/metrics.py `_BUCKETS` (inclusive upper bounds).
_BUCKET_ORDER = ["h1", "h2-7", "h8-30", "h31-90", "h91-365", "h366+"]


def _csv(raw: str) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def read_metrics(
    session: Session, code: str, *, scenario: str | None, models: str | None
) -> list[MetricGroup]:
    sid = session.execute(select(Series.id).where(Series.code == code)).scalar_one_or_none()
    if sid is None:
        raise HTTPException(status_code=404, detail=f"series '{code}' not found")

    err = Forecast.value - Observation.value  # forecast - actual (matches metrics.bias sign)
    horizon = Forecast.target_date - Forecast.made_on  # Postgres: date - date = integer days
    bucket = case(
        (horizon <= 1, "h1"),
        (horizon <= 7, "h2-7"),
        (horizon <= 30, "h8-30"),
        (horizon <= 90, "h31-90"),
        (horizon <= 365, "h91-365"),
        else_="h366+",
    ).label("bucket")

    stmt = (
        select(
            Forecast.scenario,
            Forecast.model_run_id,
            bucket,
            func.count().label("n"),
            func.avg(func.abs(err)).label("mae"),
            func.sqrt(func.avg(err * err)).label("rmse"),
            func.avg(err).label("bias"),
        )
        .join(
            Observation,
            and_(
                Observation.series_id == Forecast.series_id,
                Observation.obs_date == Forecast.target_date,
            ),
        )
        .where(Forecast.series_id == sid, Forecast.target_date > Forecast.made_on)
    )
    if scenario:
        stmt = stmt.where(Forecast.scenario.in_(_csv(scenario)))
    if models:
        stmt = stmt.where(Forecast.model_run_id.in_(_csv(models)))
    stmt = stmt.group_by(Forecast.scenario, Forecast.model_run_id, bucket)

    by_group: dict[tuple[str, str], dict[str, MetricBucket]] = {}
    for scen, mrid, bkt, n, mae, rmse, bias in session.execute(stmt):
        by_group.setdefault((str(scen), str(mrid)), {})[str(bkt)] = MetricBucket(
            bucket=str(bkt), n=int(n), mae=float(mae), rmse=float(rmse), bias=float(bias)
        )
    return [
        MetricGroup(
            scenario=scen,
            model_run_id=mrid,
            buckets=[buckets[b] for b in _BUCKET_ORDER if b in buckets],
        )
        for (scen, mrid), buckets in by_group.items()
    ]
