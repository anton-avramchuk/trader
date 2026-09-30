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

from tests.test_aggregate_job import loaded_contract
from tests.test_iss_jobs import run_job
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


def prepared(iss_worker: Worker, factory: sessionmaker[Session], root_id: int) -> int:
    contract_id = loaded_contract(iss_worker, factory, root_id)
    run_job(iss_worker, factory, "aggregate.contract", contract_id=contract_id)
    return contract_id


def test_run_on_contract_then_repeat_is_unchanged(
    iss_worker: Worker, session_factory: sessionmaker[Session], root_id: int
) -> None:
    contract_id = prepared(iss_worker, session_factory, root_id)

    first = run_job(
        iss_worker,
        session_factory,
        "engine.run",
        engine="job_highs",
        contract_id=contract_id,
        timeframe="1h",
    )
    again = run_job(
        iss_worker,
        session_factory,
        "engine.run",
        engine="job_highs",
        contract_id=contract_id,
        timeframe="1h",
    )

    assert first.status == "succeeded", first.error
    assert first.result["outcome"] == "created" and first.result["events_written"] > 0
    assert first.result["bars_new"] == first.result["bars_total"] > 2
    assert again.result["outcome"] == "unchanged"
    assert again.result["run_id"] == first.result["run_id"]
    with session_factory() as session:
        events = load_events(session, first.result["run_id"])
        run = session.get(EngineRun, first.result["run_id"])
    assert len(events) == first.result["events_written"]
    assert run is not None and run.dataset_version_id is not None


def test_full_mode_and_continuous_series(
    iss_worker: Worker, session_factory: sessionmaker[Session], root_id: int
) -> None:
    contract_id = prepared(iss_worker, session_factory, root_id)
    common: dict[str, Any] = {"engine": "job_highs", "timeframe": "15m"}

    first = run_job(
        iss_worker, session_factory, "engine.run", contract_id=contract_id, **common
    )
    full = run_job(
        iss_worker,
        session_factory,
        "engine.run",
        contract_id=contract_id,
        mode="full",
        **common,
    )
    continuous = run_job(
        iss_worker, session_factory, "engine.run", root_id=root_id, **common
    )

    assert full.result["outcome"] == "created"
    assert full.result["run_id"] != first.result["run_id"]
    assert continuous.status == "succeeded", continuous.error
    with session_factory() as session:
        run = session.get(EngineRun, continuous.result["run_id"])
        runs = session.scalar(select(func.count()).select_from(EngineRun))
    assert run is not None and run.root_id == root_id and run.dataset_version_id is None
    assert runs == 3


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"engine": "job_highs", "timeframe": "15m"}, "root_id или contract_id"),
        (
            {
                "engine": "job_highs",
                "timeframe": "15m",
                "root_id": 1,
                "contract_id": 1,
            },
            "root_id или contract_id",
        ),
        ({"engine": "job_highs", "contract_id": 1, "timeframe": "2h"}, "таймфрейм"),
        ({"engine": "nope", "contract_id": 1, "timeframe": "15m"}, "nope"),
        (
            {
                "engine": "job_highs",
                "engine_params": {"x": 1},
                "contract_id": 1,
                "timeframe": "15m",
                "mode": "sometimes",
            },
            "Неизвестный режим",
        ),
        (
            {"engine": "job_highs", "contract_id": 999_999, "timeframe": "15m"},
            "не найден",
        ),
        ({"engine": "job_highs", "root_id": 999_999, "timeframe": "15m"}, "не найден"),
    ],
)
def test_bad_input_gives_a_readable_error(
    iss_worker: Worker,
    session_factory: sessionmaker[Session],
    params: dict[str, Any],
    message: str,
) -> None:
    job = run_job(iss_worker, session_factory, "engine.run", **params)

    assert job.status == "failed"
    assert message in (job.error or "")


def test_contract_without_bars_is_a_readable_error(
    iss_worker: Worker, session_factory: sessionmaker[Session], root_id: int
) -> None:
    from tests.test_iss_jobs import contract_id_of

    run_job(
        iss_worker,
        session_factory,
        "iss.sync_root",
        root_id=root_id,
        enqueue_imports=False,
    )

    job = run_job(
        iss_worker,
        session_factory,
        "engine.run",
        engine="job_highs",
        contract_id=contract_id_of(session_factory, "BRZ6"),
        timeframe="15m",
    )

    assert job.status == "failed" and "Нет баров" in (job.error or "")
