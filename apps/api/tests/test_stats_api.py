"""REST исторической статистики: исходы вхождений, фильтры, as-of (нужна БД)."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session
from trader_db import advance_run, make_engine, read_bars
from trader_engine.events import EventEngine, register, unregister
from trader_engine.indicators import BarInput

from tests.seeding import Seed, iso
from trader_api import stats_api

EVERY = 6  # новое вхождение каждые 6 баров: detected на i % 6 == 0, confirmed на +1


class Params(BaseModel):
    pass


class Patterns(EventEngine):
    """Тестовый движок: «паттерн» каждые 6 баров, подтверждается следующим баром."""

    name = "api_stats_patterns"
    title = "Паттерны статистики (тест)"
    Params = Params

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        self.index = 0
        self.pending: int | None = None
        self.chain: int | None = None
        self.next_id = 1

    def on_bar(self, bar: BarInput) -> None:
        if self.index % EVERY == 0 and self.index > 0:
            self.pending = self.next_id
            self.next_id += 1
            self.chain = self._emit(bar, "detected", "candidate", None)
        elif self.index % EVERY == 1 and self.pending is not None:
            self.chain = self._emit(
                bar, "confirmed", "confirmed", bar.close + 5, self.chain
            )
            self.pending = None
        self.index += 1

    def _emit(
        self,
        bar: BarInput,
        status: Any,
        state: str,
        target: float | None,
        revises: int | None = None,
    ) -> int:
        event = self.emit(
            "pattern",
            status,
            {
                "id": self.pending,
                "pattern": "double_bottom",
                "direction": "bullish",
                "state": state,
                "start": bar.timestamp.isoformat(),
                "end": bar.timestamp.isoformat(),
                "points": [{"role": "a"}, {"role": "b"}, {"role": "c"}],
                "line": {"p1": 1, "p2": 1, "t1": None, "t2": None},
                "height": 5,
                "target": target,
                "features": {},
                "quality": {"score": 70.0, "components": {}},
            },
            revises=revises,
        )
        return event.seq

    def get_state(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "pending": self.pending,
            "chain": self.chain,
            "next_id": self.next_id,
        }

    def set_state(self, state: dict[str, Any]) -> None:
        self.index = state["index"]
        self.pending = state["pending"]
        self.chain = state["chain"]
        self.next_id = state["next_id"]


@pytest.fixture(autouse=True)
def engine_registered() -> Iterator[None]:
    register(Patterns)
    stats_api._cache.clear()
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


def bars_of(database_url: str, seed: Seed) -> list[BarInput]:
    engine = make_engine(database_url)
    with Session(engine) as session:
        bars = [BarInput.from_bar(bar) for bar in read_bars(session, seed.a, "15m")]
    engine.dispose()
    return bars


def stats(client: TestClient, run_id: int, **params: Any) -> dict[str, Any]:
    response = client.get("/stats/outcomes", params={"run_id": run_id} | params)
    assert response.status_code == 200, response.text
    return response.json()


def test_default_horizons_and_counts(client: TestClient, run_id: int) -> None:
    body = stats(client, run_id, baseline="false")

    assert body["matched"] >= 3
    assert body["unit"] == "atr"
    [bucket] = body["buckets"]
    assert bucket["key"] == "all" and bucket["n_occurrences"] == body["matched"]
    assert [h["horizon"] for h in bucket["horizons"]] == [5, 10, 20, 50]
    short = bucket["horizons"][0]
    assert short["n_raw"] >= 1
    assert short["n_effective"] <= short["n_raw"]
    assert "no_baseline" not in short["warnings"]  # baseline не запрашивали
    longest = bucket["horizons"][-1]
    assert longest["censored"] >= 1  # на коротких данных горизонт 50 не доходит


def test_small_sample_is_flagged(
    client: TestClient, run_id: int, seed: Seed, database_url: str
) -> None:
    bars = bars_of(database_url, seed)
    cut = bars[len(bars) // 3].close_time

    [bucket] = stats(client, run_id, as_of=iso(cut), baseline="false")["buckets"]

    assert all("small_sample" in h["warnings"] for h in bucket["horizons"])


def test_deoverlap_thins_longer_horizons(client: TestClient, run_id: int) -> None:
    [bucket] = stats(client, run_id, horizon=[3, 12], baseline="false")["buckets"]

    short, long = bucket["horizons"]
    assert short["n_effective"] == short["n_raw"]  # шаг 6 ≥ горизонта 3
    assert long["n_effective"] < long["n_raw"]  # горизонт 12 перекрывает соседей


def test_filters_and_grouping(client: TestClient, run_id: int) -> None:
    whole = stats(client, run_id, horizon=[3], baseline="false")

    assert stats(client, run_id, group="double_top")["matched"] == 0
    assert stats(client, run_id, direction="bearish")["matched"] == 0
    assert stats(client, run_id, quality_min=90)["matched"] == 0
    assert stats(client, run_id, quality_min=60)["matched"] == whole["matched"]
    grouped = stats(client, run_id, horizon=[3], group_by="group", baseline="false")
    assert [b["key"] for b in grouped["buckets"]] == ["double_bottom"]


def test_candidates_need_the_flag(client: TestClient, run_id: int) -> None:
    confirmed = stats(client, run_id, baseline="false")["matched"]
    with_candidates = stats(
        client, run_id, include_candidates="true", baseline="false"
    )["matched"]

    # каждое вхождение подтверждается, поэтому кандидатов без подтверждения нет
    assert with_candidates == confirmed


def test_percent_unit(client: TestClient, run_id: int) -> None:
    body = stats(client, run_id, unit="pct", horizon=[3], baseline="false")

    assert body["unit"] == "pct"
    assert body["buckets"][0]["horizons"][0]["unit"] == "pct"


def test_as_of_hides_later_occurrences(
    client: TestClient, run_id: int, seed: Seed, database_url: str
) -> None:
    bars = bars_of(database_url, seed)
    cut = bars[len(bars) // 2].close_time

    whole = stats(client, run_id, baseline="false")
    early = stats(client, run_id, as_of=iso(cut), baseline="false")

    assert 0 < early["matched"] < whole["matched"]


def test_result_is_cached_and_stable(client: TestClient, run_id: int) -> None:
    first = stats(client, run_id, seed=3)
    assert len(stats_api._cache) == 1

    again = stats(client, run_id, seed=3)

    assert first == again
    assert len(stats_api._cache) == 1
    stats(client, run_id, seed=4)
    assert len(stats_api._cache) == 2


def test_baseline_is_optional(client: TestClient, run_id: int) -> None:
    off = stats(client, run_id, baseline="false", horizon=[3])
    horizon = off["buckets"][0]["horizons"][0]

    assert horizon["baseline_n"] == 0 and horizon["edge"] is None


def test_parameter_errors(client: TestClient, run_id: int) -> None:
    def status(**params: Any) -> int:
        return client.get("/stats/outcomes", params=params).status_code

    assert status(run_id=999999) == 404
    assert status() == 422  # нет run_id
    assert status(run_id=run_id, horizon=[0]) == 422
    assert status(run_id=run_id, horizon=[501]) == 422
    assert status(run_id=run_id, horizon=list(range(1, 11))) == 422
    assert status(run_id=run_id, group_by="nonsense") == 422


def test_occurrence_detail(client: TestClient, run_id: int) -> None:
    response = client.get(
        "/stats/occurrence",
        params={"run_id": run_id, "key": f"{Patterns.name}:1", "horizon": [2, 3]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["occurrence"]["group"] == "double_bottom"
    assert body["occurrence"]["entry"] == "confirmed"
    assert body["occurrence"]["target"] is not None
    assert [o["horizon"] for o in body["outcomes"]] == [2, 3]
    assert body["regime"] == "unknown"  # на коротких данных режима ещё нет
    assert body["outcomes"][0]["ret_pct"] is not None


def test_occurrence_not_found(client: TestClient, run_id: int) -> None:
    missing = client.get(
        "/stats/occurrence", params={"run_id": run_id, "key": "nope:1"}
    )
    no_run = client.get("/stats/occurrence", params={"run_id": 999999, "key": "x"})

    assert missing.status_code == 404 and no_run.status_code == 404
