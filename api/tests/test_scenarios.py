from __future__ import annotations


def test_list_scenarios_and_kind_filter(client, factory) -> None:
    factory.scenario("MEAN", kind="weather")
    factory.scenario(
        "coldsnap",
        kind="custom",
        adjustments=[{"select": {"group": "demand"}, "type": "PERCENT", "value": 1.1}],
        weather_years=["*"],
    )

    alls = {s["code"] for s in client.get("/scenarios").json()}
    assert alls == {"MEAN", "coldsnap"}

    customs = client.get("/scenarios", params={"kind": "custom"}).json()
    assert len(customs) == 1
    assert customs[0]["adjustments"][0]["type"] == "PERCENT"
    assert customs[0]["weather_years"] == ["*"]
