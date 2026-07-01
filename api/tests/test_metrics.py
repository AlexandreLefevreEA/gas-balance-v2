from __future__ import annotations

import datetime as dt


def test_metrics_buckets(client, factory) -> None:
    sid = factory.series("CE.54", category="demand", area="DE")
    made = dt.date(2024, 1, 1)
    # horizon 1 (h1): forecast 12 vs actual 10 -> err +2
    factory.observation(sid, dt.date(2024, 1, 2), 10.0)
    factory.forecast(sid, "MEAN", dt.date(2024, 1, 2), made, 12.0)
    # horizon 5 (h2-7): forecast 8 vs actual 10 -> err -2
    factory.observation(sid, dt.date(2024, 1, 6), 10.0)
    factory.forecast(sid, "MEAN", dt.date(2024, 1, 6), made, 8.0)

    body = client.get("/metrics", params={"code": "CE.54"}).json()
    assert len(body) == 1
    buckets = {b["bucket"]: b for b in body[0]["buckets"]}
    assert buckets["h1"]["mae"] == 2.0
    assert buckets["h1"]["bias"] == 2.0
    assert buckets["h2-7"]["mae"] == 2.0
    assert buckets["h2-7"]["bias"] == -2.0


def test_metrics_only_where_actual_exists(client, factory) -> None:
    sid = factory.series("CE.54")
    # forecast with NO matching actual -> excluded by the inner join
    factory.forecast(sid, "MEAN", dt.date(2024, 1, 2), dt.date(2024, 1, 1), 12.0)
    assert client.get("/metrics", params={"code": "CE.54"}).json() == []


def test_metrics_unknown_code_404(client) -> None:
    assert client.get("/metrics", params={"code": "NOPE"}).status_code == 404
