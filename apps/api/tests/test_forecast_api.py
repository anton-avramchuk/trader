"""REST Forecast: Empirical и KNN, окно и вхождение, as_of, кэш, ошибки (нужна БД)."""

from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from trader_db import advance_run, make_engine, read_bars
from trader_engine.events import register, unregister
from trader_engine.indicators import BarInput

from tests.seeding import Seed, iso
from tests.test_stats_api import Patterns, bars_of
from trader_api import forecast_api

PARAMS: dict[str, Any] = {"window": 12, "pip_points": 5, "horizon": [2, 3]}


@pytest.fixture(autouse=True)
def engine_registered() -> Iterator[None]:
    register(Patterns)
    forecast_api._cache.clear()  # pyright: ignore[reportPrivateUsage]
    yield
    unregister(Patterns.name)


@pytest.fixture
def run_id(seed: Seed, database_url: str) -> Iterator[int]:
    engine = make_engine(database_url)
    with Session(engine) as session:
        bars = [BarInput.from_bar(bar) for bar in read_bars(session, seed.a, "15m")]
        outcome = advance_run(
            session, Patterns.name, {}, bars, "15m", contract_id=seed.a
        )
        session.commit()
    engine.dispose()
    yield outcome.run_id


def get(client: TestClient, run_id: int, **params: Any) -> dict[str, Any]:
    response = client.get(
        "/forecast", params={"query_run_id": run_id} | PARAMS | params
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_window_query_has_knn_only(client: TestClient, run_id: int) -> None:
    body = get(client, run_id)

    assert body["empirical"] is None
    assert "empirical_needs_occurrence" in body["warnings"]
    assert body["occurrence_key"] is None and body["direction"] is None
    knn = body["knn"]
    assert knn["method"] == "knn" and knn["sample"] >= 1
    assert [h["horizon"] for h in knn["horizons"]] == [2, 3]
    assert body["thresholds"] == [0.5, 1.0, 2.0]


def test_occurrence_query_has_both_methods(client: TestClient, run_id: int) -> None:
    body = get(client, run_id, key=f"{Patterns.name}:6")

    assert body["direction"] == "bullish"
    empirical = body["empirical"]
    assert empirical["method"] == "empirical" and empirical["sample"] >= 1
    first = empirical["horizons"][0]
    assert {p["threshold"] for p in first["probabilities"]} == {0.5, 1.0, 2.0}
    assert [q["q"] for q in first["quantiles"]] == [10, 25, 75, 90]
    assert first["n_effective"] <= first["n_raw"]
    for p in first["probabilities"]:
        assert 0 <= p["up"] <= 1 and 0 <= p["down"] <= 1
    assert body["knn"]["sample"] >= 1


def test_custom_thresholds_and_unit(client: TestClient, run_id: int) -> None:
    body = get(
        client, run_id, key=f"{Patterns.name}:6", unit="pct", threshold=[0.1, 0.3]
    )

    assert body["unit"] == "pct" and body["thresholds"] == [0.1, 0.3]
    horizon = body["empirical"]["horizons"][0]
    assert horizon["unit"] == "pct"
    assert [p["threshold"] for p in horizon["probabilities"]] == [0.1, 0.3]


def test_as_of_limits_known_history(
    client: TestClient, run_id: int, seed: Seed, database_url: str
) -> None:
    bars = bars_of(database_url, seed)
    key = f"{Patterns.name}:6"
    middle = bars[len(bars) // 2].close_time
    late = bars[len(bars) * 5 // 6].close_time

    at_entry = get(client, run_id, key=key)
    mid = get(client, run_id, key=key, as_of=iso(middle))
    end = get(client, run_id, key=key, as_of=iso(late))

    assert datetime.fromisoformat(mid["as_of"]) == middle
    # чем позже as_of, тем больше известной истории (по умолчанию — момент входа)
    samples = [r["empirical"]["sample"] for r in (at_entry, mid, end)]
    assert samples == sorted(samples) and samples[0] < samples[-1]


def test_result_is_cached_and_stable(client: TestClient, run_id: int) -> None:
    first = get(client, run_id)
    assert len(forecast_api._cache) == 1  # pyright: ignore[reportPrivateUsage]

    assert get(client, run_id) == first
    get(client, run_id, unit="pct")
    assert len(forecast_api._cache) == 2  # pyright: ignore[reportPrivateUsage]


def test_errors(client: TestClient, run_id: int) -> None:
    def status(**params: Any) -> int:
        return client.get("/forecast", params=params).status_code

    assert status(query_run_id=999999) == 404
    assert status() == 422
    assert status(query_run_id=run_id, key="nope:1") == 404
    assert status(query_run_id=run_id, threshold=[0]) == 422
    assert status(query_run_id=run_id, threshold=[100]) == 422
    assert status(query_run_id=run_id, threshold=list(range(1, 9))) == 422
    assert status(query_run_id=run_id, horizon=[0]) == 422
    assert status(query_run_id=run_id, window=5) == 422
