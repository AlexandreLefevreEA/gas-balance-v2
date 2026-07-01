from __future__ import annotations

import datetime as dt


def test_list_series_and_filters(client, factory) -> None:
    factory.series("EU.DEMAND", category="balance", area=None)
    factory.series("CE.54", category="demand", sub_group="LDZ", area="DE")
    factory.series("OLD", category="demand", area="DE", is_active=False)

    codes = {s["code"] for s in client.get("/series").json()}
    assert {"EU.DEMAND", "CE.54"} <= codes
    assert "OLD" not in codes  # active defaults to true

    de = client.get("/series", params={"area": "DE"}).json()
    assert {s["code"] for s in de} == {"CE.54"}

    incl_inactive = {s["code"] for s in client.get("/series", params={"active": "false"}).json()}
    assert "OLD" in incl_inactive


def test_observations_batch_range_and_unknown(client, factory) -> None:
    sid = factory.series("EU.DEMAND")
    for i, v in enumerate([10.0, 20.0, 30.0]):
        factory.observation(sid, dt.date(2024, 1, 1 + i), v)

    body = client.get("/observations", params={"codes": "EU.DEMAND,NOPE"}).json()
    assert len(body) == 1  # unknown code silently omitted
    assert body[0]["code"] == "EU.DEMAND"
    assert len(body[0]["points"]) == 3

    ranged = client.get("/observations", params={"codes": "EU.DEMAND", "from": "2024-01-02"}).json()
    assert [p["value"] for p in ranged[0]["points"]] == [20.0, 30.0]


def test_observations_requires_codes(client) -> None:
    assert client.get("/observations").status_code == 422


def test_covariates_daily_mean(client, factory) -> None:
    sid = factory.series("KP.TEMP.DE", category="temperature", area="DE")
    factory.covariate(sid, dt.datetime(2024, 1, 1, 0, tzinfo=dt.UTC), 4.0)
    factory.covariate(sid, dt.datetime(2024, 1, 1, 12, tzinfo=dt.UTC), 6.0)

    body = client.get("/covariates", params={"codes": "KP.TEMP.DE"}).json()
    assert body[0]["points"] == [{"date": "2024-01-01", "value": 5.0}]


def test_forecast_covariates_latest_run_only_daily_mean(client, factory) -> None:
    sid = factory.series("KP.TEMPFC.DE.EC_46", category="temperature_forecast", area="DE")
    # Older run forecast an earlier hour — must be excluded (not backfilled into history).
    factory.forecast_covariate(
        sid, dt.date(2023, 12, 30), dt.datetime(2023, 12, 31, tzinfo=dt.UTC), 99.0
    )
    # Latest run: 00:00 -> 4.0, 12:00 -> 6.0 => daily mean 5.0, and only this day is returned.
    factory.forecast_covariate(
        sid, dt.date(2023, 12, 31), dt.datetime(2024, 1, 1, 0, tzinfo=dt.UTC), 4.0
    )
    factory.forecast_covariate(
        sid, dt.date(2023, 12, 31), dt.datetime(2024, 1, 1, 12, tzinfo=dt.UTC), 6.0
    )

    body = client.get("/forecast-covariates", params={"codes": "KP.TEMPFC.DE.EC_46"}).json()
    assert body[0]["points"] == [{"date": "2024-01-01", "value": 5.0}]
