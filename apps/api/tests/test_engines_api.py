"""REST движков событий: каталог, прогоны, события as-of (нужна БД)."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session
from trader_db import advance_run, make_engine
from trader_engine.events import EventEngine, register, unregister
from trader_engine.indicators import BarInput

from tests.seeding import Seed, iso
from tests.seeding import get as get_object

START = datetime(2026, 9, 28, 4, tzinfo=UTC)
CLOSES = [10.0, 11, 12, 8, 7, 13, 12, 9, 15, 14]


class Params(BaseModel):
    step: float = 0.0


class Highs(EventEngine):
    name = "api_highs"
    title = "Максимумы (тест)"
    Params = Params

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        self.best: float | None = None
        self.last: int | None = None

    def on_bar(self, bar: BarInput) -> None:
        if self.best is None or bar.close > self.best:
            status = "detected" if self.last is None else "revised"
            event = self.emit("high", status, {"price": bar.close}, revises=self.last)
            self.best, self.last = bar.close, event.seq
        elif bar.close < self.best - 3:
            self.emit("drop", "detected", {"price": bar.close})

    def get_state(self) -> dict[str, Any]:
        return {"best": self.best, "last": self.last}

    def set_state(self, state: dict[str, Any]) -> None:
        self.best, self.last = state["best"], state["last"]


def get(client: TestClient, path: str, **params: Any) -> list[dict[str, Any]]:
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def at(value: str) -> datetime:
    return datetime.fromisoformat(value)


@pytest.fixture(autouse=True)
def highs() -> Iterator[None]:
    register(Highs)
    yield
    unregister(Highs.name)


def bars() -> list[BarInput]:
    result: list[BarInput] = []
    for index, close in enumerate(CLOSES):
        opened = START + timedelta(minutes=15 * index)
        result.append(
            BarInput(
                timestamp=opened,
                close_time=opened + timedelta(minutes=15),
                open=close,
                high=close,
                low=close,
                close=close,
                volume=1.0,
                trading_day=opened.date(),
            )
        )
    return result


@pytest.fixture
def run_id(seed: Seed, database_url: str) -> int:
    engine = make_engine(database_url)
    with Session(engine) as session:
        result = advance_run(
            session, "api_highs", None, bars(), "15m", instrument_id=seed.id
        )
        session.commit()
    engine.dispose()
    return result.run_id


def test_catalog_lists_registered_engines(client: TestClient) -> None:
    (info,) = [e for e in get(client, "/engines") if e["name"] == "api_highs"]

    assert info["title"] == "Максимумы (тест)" and info["version"] == 1
    assert info["defaults"] == {"step": 0.0}
    assert info["params_schema"]["properties"]["step"]["type"] == "number"


class TestRuns:
    def test_list_and_get_a_run(
        self, client: TestClient, seed: Seed, run_id: int
    ) -> None:
        (run,) = get(client, "/engine-runs")

        assert run == get_object(client, f"/engine-runs/{run_id}")
        assert run["engine"] == "api_highs" and run["timeframe"] == "15m"
        assert run["instrument_id"] == seed.id
        assert run["bars_processed"] == len(CLOSES) and run["event_count"] == 8
        assert run["params"] == {"step": 0.0}

    def test_filters(self, client: TestClient, seed: Seed, run_id: int) -> None:
        assert (
            len(get(client, "/engine-runs", engine="api_highs", timeframe="15m")) == 1
        )
        assert get(client, "/engine-runs", engine="other") == []
        assert get(client, "/engine-runs", instrument_id=seed.id + 1) == []
        assert len(get(client, "/engine-runs", instrument_id=seed.id)) == 1

    def test_unknown_run_is_404(self, client: TestClient) -> None:
        assert client.get("/engine-runs/999").status_code == 404
        assert client.get("/engine-runs/999/events").status_code == 404


class TestEvents:
    def test_history_returns_every_event_in_order(
        self, client: TestClient, run_id: int
    ) -> None:
        events = get(client, f"/engine-runs/{run_id}/events")

        assert [e["seq"] for e in events] == list(range(8))
        assert [e["status"] for e in events][:3] == ["detected", "revised", "revised"]
        assert events[1]["revises"] == 0 and events[0]["revises"] is None
        assert at(events[0]["available_at"]) == bars()[0].close_time

    def test_current_view_keeps_latest_version_of_each_chain(
        self, client: TestClient, run_id: int
    ) -> None:
        current = get(client, f"/engine-runs/{run_id}/events", view="current")

        assert [(e["kind"], e["payload"]["price"]) for e in current] == [
            ("high", 15.0),
            ("drop", 8.0),
            ("drop", 7.0),
            ("drop", 9.0),
        ]

    def test_as_of_hides_the_future(self, client: TestClient, run_id: int) -> None:
        moment = bars()[5].close_time  # закрытие бара с максимумом 13

        history = get(client, f"/engine-runs/{run_id}/events", as_of=iso(moment))
        current = get(
            client, f"/engine-runs/{run_id}/events", as_of=iso(moment), view="current"
        )

        assert history[-1]["payload"]["price"] == 13
        assert all(at(e["available_at"]) <= moment for e in history)
        assert [(e["kind"], e["payload"]["price"]) for e in current] == [
            ("high", 13.0),
            ("drop", 8.0),
            ("drop", 7.0),
        ]
        assert get(client, f"/engine-runs/{run_id}/events", as_of=iso(START)) == []

    def test_kind_and_after_seq_filters(self, client: TestClient, run_id: int) -> None:
        drops = get(client, f"/engine-runs/{run_id}/events", kind="drop")
        tail = get(client, f"/engine-runs/{run_id}/events", after_seq=4)
        tail_current = get(
            client, f"/engine-runs/{run_id}/events", after_seq=4, view="current"
        )

        assert {e["kind"] for e in drops} == {"drop"} and len(drops) == 3
        assert [e["seq"] for e in tail] == [5, 6, 7]
        assert [e["seq"] for e in tail_current] == [7, 6]  # порядок — по цепочкам

    def test_invalid_query_is_422(self, client: TestClient, run_id: int) -> None:
        url = f"/engine-runs/{run_id}/events"

        assert client.get(url, params={"view": "all"}).status_code == 422
        assert (
            client.get(url, params={"as_of": "2026-09-28T10:00:00"}).status_code == 422
        )
        assert client.get(url, params={"after_seq": -1}).status_code == 422
