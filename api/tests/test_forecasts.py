from __future__ import annotations

import datetime as dt


def _points(body, code, scenario):
    for s in body:
        if s["code"] == code and s["scenario"] == scenario:
            return s["points"]
    return None


def test_latest_collapses_vintages(client, factory) -> None:
    sid = factory.series("EU.DEMAND")
    target = dt.date(2024, 6, 1)
    factory.forecast(sid, "MEAN", target, dt.date(2024, 5, 1), 100.0)
    factory.forecast(sid, "MEAN", target, dt.date(2024, 5, 20), 110.0)  # newer vintage

    latest = client.get("/forecasts", params={"codes": "EU.DEMAND", "scenario": "MEAN"}).json()
    pts = _points(latest, "EU.DEMAND", "MEAN")
    assert len(pts) == 1
    assert pts[0]["value"] == 110.0
    assert pts[0]["made_on"] is None  # omitted for the latest view

    allv = client.get(
        "/forecasts", params={"codes": "EU.DEMAND", "scenario": "MEAN", "made_on": "all"}
    ).json()
    pts_all = _points(allv, "EU.DEMAND", "MEAN")
    assert len(pts_all) == 2
    assert {p["made_on"] for p in pts_all} == {"2024-05-01", "2024-05-20"}


def test_bad_made_on_is_422(client, factory) -> None:
    factory.series("EU.DEMAND")
    r = client.get("/forecasts", params={"codes": "EU.DEMAND", "made_on": "nonsense"})
    assert r.status_code == 422
