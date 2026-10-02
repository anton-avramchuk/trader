"""Задача engine.run: прогон движка событий по барам из БД."""

from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from trader_db import load_events
from trader_db.models import EngineRun
from trader_engine.events import EventEngine, register, unregister
from trader_engine.indicators import BarInput

from tests.conftest import seed_candles
from tests.helpers import run_job
from trader_worker.runner import Worker


class Params(BaseModel):
    pass


class NewHighs(EventEngine):
    name = "job_highs"
    title = "Максимумы (тест)"
    Params = Params

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        self.best: float | None = None
        self.last: int | None = None

    def on_bar(self, bar: BarInput) -> None:
        if self.best is None or bar.high > self.best:
            status = "detected" if self.last is None else "revised"
            event = self.emit("high", status, {"price": bar.high}, revises=self.last)
            self.best, self.last = bar.high, event.seq

    def get_state(self) -> dict[str, Any]:
        return {"best": self.best, "last": self.last}

    def set_state(self, state: dict[str, Any]) -> None:
        self.best, self.last = state["best"], state["last"]


@pytest.fixture(autouse=True)
def engine_registered() -> Iterator[None]:
    register(NewHighs)
    yield
    unregister(NewHighs.name)


def worker_with_engine(
    loader_worker: Worker, session_factory: sessionmaker[Session], instrument_id: int
) -> Worker:
    seed_candles(session_factory, instrument_id, "1h", 60)
    seed_candles(session_factory, instrument_id, "15m", 120)
    return loader_worker


def test_run_then_repeat_is_unchanged(
    loader_worker: Worker, session_factory: sessionmaker[Session], instrument_id: int
) -> None:
    worker = worker_with_engine(loader_worker, session_factory, instrument_id)
    common: dict[str, Any] = {
        "engine": "job_highs",
        "instrument_id": instrument_id,
        "timeframe": "1h",
    }

    first = run_job(worker, session_factory, "engine.run", **common)
    again = run_job(worker, session_factory, "engine.run", **common)

    assert first.status == "succeeded", first.error
    assert first.result["outcome"] == "created" and first.result["events_written"] > 0
    assert first.result["bars_new"] == first.result["bars_total"] == 60
    assert again.result["outcome"] == "unchanged"
    assert again.result["run_id"] == first.result["run_id"]
    with session_factory() as session:
        events = load_events(session, first.result["run_id"])
        run = session.get(EngineRun, first.result["run_id"])
    assert len(events) == first.result["events_written"]
    assert run is not None and run.instrument_id == instrument_id


def test_full_mode_and_separate_timeframes(
    loader_worker: Worker, session_factory: sessionmaker[Session], instrument_id: int
) -> None:
    worker = worker_with_engine(loader_worker, session_factory, instrument_id)
    common: dict[str, Any] = {"engine": "job_highs", "instrument_id": instrument_id}

    first = run_job(worker, session_factory, "engine.run", timeframe="15m", **common)
    full = run_job(
        worker, session_factory, "engine.run", timeframe="15m", mode="full", **common
    )
    other = run_job(worker, session_factory, "engine.run", timeframe="1h", **common)

    assert full.result["outcome"] == "created"
    assert full.result["run_id"] != first.result["run_id"]
    assert other.status == "succeeded", other.error
    with session_factory() as session:
        runs = session.scalar(select(func.count()).select_from(EngineRun))
    assert runs == 3


def test_continuation_after_new_candles(
    loader_worker: Worker, session_factory: sessionmaker[Session], instrument_id: int
) -> None:
    seed_candles(session_factory, instrument_id, "15m", 50)
    common: dict[str, Any] = {
        "engine": "job_highs",
        "instrument_id": instrument_id,
        "timeframe": "15m",
    }
    first = run_job(loader_worker, session_factory, "engine.run", **common)

    seed_candles(session_factory, instrument_id, "15m", 80)  # те же 50 + новые 30
    second = run_job(loader_worker, session_factory, "engine.run", **common)

    assert first.result["bars_total"] == 50
    assert second.result["outcome"] == "continued"
    assert (
        second.result["bars_new"] == 30
        and second.result["run_id"] == first.result["run_id"]
    )


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"engine": "job_highs", "timeframe": "15m"}, "instrument_id"),
        ({"engine": "job_highs", "instrument_id": 1, "timeframe": "2h"}, "таймфрейм"),
        ({"engine": "nope", "instrument_id": 1, "timeframe": "15m"}, "nope"),
        (
            {
                "engine": "job_highs",
                "engine_params": {"x": 1},
                "instrument_id": 1,
                "timeframe": "15m",
                "mode": "sometimes",
            },
            "Неизвестный режим",
        ),
        (
            {"engine": "job_highs", "instrument_id": 999_999, "timeframe": "15m"},
            "не найден",
        ),
    ],
)
def test_bad_input_gives_a_readable_error(
    loader_worker: Worker,
    session_factory: sessionmaker[Session],
    params: dict[str, Any],
    message: str,
) -> None:
    job = run_job(loader_worker, session_factory, "engine.run", **params)

    assert job.status == "failed"
    assert message in (job.error or "")


def test_instrument_without_candles_is_a_readable_error(
    loader_worker: Worker, session_factory: sessionmaker[Session], instrument_id: int
) -> None:
    job = run_job(
        loader_worker,
        session_factory,
        "engine.run",
        engine="job_highs",
        instrument_id=instrument_id,
        timeframe="15m",
    )

    assert job.status == "failed" and "Нет свечей" in (job.error or "")


def test_last_bars_limits_the_window_and_zero_means_all(
    loader_worker: Worker, session_factory: sessionmaker[Session], instrument_id: int
) -> None:
    worker = worker_with_engine(loader_worker, session_factory, instrument_id)
    common: dict[str, Any] = {
        "engine": "job_highs",
        "instrument_id": instrument_id,
        "timeframe": "1h",
    }

    window = run_job(worker, session_factory, "engine.run", last_bars=20, **common)
    everything = run_job(worker, session_factory, "engine.run", last_bars=0, **common)

    assert window.status == "succeeded", window.error
    assert window.result["bars_total"] == 20
    assert everything.result["bars_total"] == 60


@pytest.mark.parametrize("value", [-1, "10", True, 2.5])
def test_bad_last_bars_is_a_readable_error(
    loader_worker: Worker,
    session_factory: sessionmaker[Session],
    instrument_id: int,
    value: object,
) -> None:
    job = run_job(
        loader_worker,
        session_factory,
        "engine.run",
        engine="job_highs",
        instrument_id=instrument_id,
        timeframe="1h",
        last_bars=value,
    )

    assert job.status == "failed" and "last_bars" in (job.error or "")
