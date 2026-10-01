"""REST поиска аналогов: запрос-вхождение и окно, as_of, кэш, ошибки (нужна БД)."""

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
from trader_api import analogues_api

WINDOW = 12
PARAMS: dict[str, Any] = {"window": WINDOW, "pip_points": 5, "horizon": [2, 3]}


@pytest.fixture(autouse=True)
def engine_registered() -> Iterator[None]:
    register(Patterns)
    analogues_api._cache.clear()  # pyright: ignore[reportPrivateUsage]
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


def search(client: TestClient, run_id: int, **params: Any) -> dict[str, Any]:
    response = client.get(
        "/analogues", params={"query_run_id": run_id} | PARAMS | params
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_window_query_returns_matches_with_outcomes(
    client: TestClient, run_id: int
) -> None:
    body = search(client, run_id)

    assert body["occurrence_key"] is None
    assert body["unit"] == "atr"
    assert body["considered"] >= 1 and body["matches"]
    shape = body["query"]["shape"]
    assert shape["times"][0] == 0.0 and shape["times"][-1] == 1.0
    assert len(shape["values"]) == len(shape["indices"]) <= 5
    similarities = [m["similarity"] for m in body["matches"]]
    assert similarities == sorted(similarities, reverse=True)
    first = body["matches"][0]
    assert 0 < first["similarity"] <= 1
    assert [o["horizon"] for o in first["outcomes"]] == [2, 3]
    assert first["path"][0] == [0, 0]
    assert body["stats"]["matched"] == len(body["matches"])


def test_matches_do_not_overlap_the_query_window(
    client: TestClient, run_id: int
) -> None:
    body = search(client, run_id)

    for match in body["matches"]:
        assert match["formation"]["end"] <= body["query"]["start"]


def test_occurrence_query_is_causal_and_excludes_itself(
    client: TestClient, run_id: int
) -> None:
    key = f"{Patterns.name}:6"
    body = search(client, run_id, key=key)

    assert body["occurrence_key"] == key
    assert key not in [m["occurrence"]["key"] for m in body["matches"]]
    for match in body["matches"]:
        assert match["occurrence"]["available_at"] <= body["as_of"]
        assert match["formation"]["end"] <= body["query"]["start"]


def test_as_of_hides_later_history(
    client: TestClient, run_id: int, seed: Seed, database_url: str
) -> None:
    bars = bars_of(database_url, seed)
    cut = bars[len(bars) * 2 // 3].close_time

    whole = search(client, run_id)
    early = search(client, run_id, as_of=iso(cut))

    assert datetime.fromisoformat(early["as_of"]) == cut
    assert early["considered"] < whole["considered"]
    assert datetime.fromisoformat(early["query"]["end"]) <= cut


def test_result_is_cached_and_stable(client: TestClient, run_id: int) -> None:
    first = search(client, run_id)
    assert len(analogues_api._cache) == 1  # pyright: ignore[reportPrivateUsage]

    assert search(client, run_id) == first
    search(client, run_id, k=3)
    assert len(analogues_api._cache) == 2  # pyright: ignore[reportPrivateUsage]


def test_percent_normalization_switches_unit(client: TestClient, run_id: int) -> None:
    body = search(client, run_id, normalization="percent")

    assert body["unit"] == "pct"
    assert body["stats"]["unit"] == "pct"


def test_k_and_max_distance_limit_matches(client: TestClient, run_id: int) -> None:
    assert len(search(client, run_id, k=1)["matches"]) == 1
    assert search(client, run_id, max_distance=0)["matches"] == []


def test_trajectories_and_percentiles(client: TestClient, run_id: int) -> None:
    body = search(client, run_id)

    assert {p["q"] for p in body["percentiles"]} == {25, 50, 75}
    assert all(len(p["values"]) == 3 for p in body["percentiles"])
    assert body["trajectory_count"] >= 1


def test_errors(client: TestClient, run_id: int) -> None:
    def status(**params: Any) -> int:
        return client.get("/analogues", params=params).status_code

    assert status(query_run_id=999999) == 404
    assert status() == 422
    assert status(query_run_id=run_id, key="nope:1") == 404
    assert status(query_run_id=run_id, window=5) == 422
    assert status(query_run_id=run_id, window=100000) == 422
    assert status(query_run_id=run_id, normalization="zscore") == 422
    assert status(query_run_id=run_id, horizon=[0]) == 422
    assert status(query_run_id=run_id, window=400) == 422  # мало баров в ряду
