"""REST confluence-зон (нужна БД)."""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from trader_db import advance_run, make_engine, read_bars
from trader_engine.indicators import BarInput

from tests.seeding import Seed, iso

PARAMS = {"swing": False, "fibonacci": False, "atr_period": 5}


@pytest.fixture
def run_bars(seed: Seed, database_url: str) -> list[BarInput]:
    """Прогон levels (только pivot/prev) по 15-минутным барам контракта A."""
    engine = make_engine(database_url)
    with Session(engine) as session:
        bars = [BarInput.from_bar(bar) for bar in read_bars(session, seed.a, "15m")]
        advance_run(session, "levels", PARAMS, bars, "15m", contract_id=seed.a)
        session.commit()
    engine.dispose()
    return bars


def zones(client: TestClient, seed: Seed, **params: Any) -> dict[str, Any]:
    query = {
        "contract_id": seed.a,
        "chart_timeframe": "15m",
        "levels_params": json.dumps(PARAMS),
    }
    response = client.get("/level-zones", params=query | params)
    assert response.status_code == 200, response.text
    return response.json()


def test_zones_cover_active_levels(
    client: TestClient, seed: Seed, run_bars: list[BarInput]
) -> None:
    body = zones(client, seed)

    assert body["atr"] > 0 and body["missing_timeframes"] == []
    assert body["zones"], body
    members = [m for z in body["zones"] for m in z["members"]]
    assert {m["family"] for m in members} <= {"pivot", "prev"}
    assert all(m["source_timeframe"] == "15m" for m in members)
    lows = [z["low"] for z in body["zones"]]
    assert lows == sorted(lows)
    for zone in body["zones"]:
        assert zone["low"] <= zone["center"] <= zone["high"]
        assert 0 <= zone["strength"] <= 100
        assert all(m["distance_atr"] >= 0 for m in zone["members"])


def test_threshold_controls_merging(
    client: TestClient, seed: Seed, run_bars: list[BarInput]
) -> None:
    tight = zones(client, seed, threshold_atr=0.001)
    wide = zones(client, seed, threshold_atr=20)

    assert len(wide["zones"]) == 1
    assert sum(len(z["members"]) for z in wide["zones"]) == sum(
        len(z["members"]) for z in tight["zones"]
    )
    assert len(tight["zones"]) > 1


def test_as_of_hides_levels_that_were_not_known_yet(
    client: TestClient, seed: Seed, run_bars: list[BarInput]
) -> None:
    first_day_end = min(
        b.close_time for b in run_bars if b.trading_day != run_bars[0].trading_day
    )
    early = zones(client, seed, as_of=iso(first_day_end.replace(hour=0, minute=0)))
    later = zones(client, seed, as_of=iso(run_bars[-1].close_time))

    assert early["zones"] == []
    assert later["zones"]


def test_missing_source_timeframe_is_reported(
    client: TestClient, seed: Seed, run_bars: list[BarInput]
) -> None:
    body = zones(client, seed, chart_timeframe="1h", source_timeframes=["1h", "4h"])

    assert body["missing_timeframes"] == ["1h", "4h"] and body["zones"] == []


def test_source_timeframe_younger_than_chart_is_422(
    client: TestClient, seed: Seed, run_bars: list[BarInput]
) -> None:
    response = client.get(
        "/level-zones",
        params={
            "contract_id": seed.a,
            "chart_timeframe": "1h",
            "source_timeframes": ["15m"],
        },
    )

    assert response.status_code == 422 and "запрещён" in response.text


def test_bad_levels_params_and_subject(client: TestClient, seed: Seed) -> None:
    base = {"chart_timeframe": "15m"}

    assert (
        client.get(
            "/level-zones", params=base | {"contract_id": seed.a, "levels_params": "[]"}
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/level-zones",
            params=base | {"contract_id": seed.a, "levels_params": '{"x": 1}'},
        ).status_code
        == 200
    )
    assert client.get("/level-zones", params=base).status_code == 422
    assert (
        client.get("/level-zones", params=base | {"contract_id": 99999}).status_code
        == 404
    )
