"""Задача aggregate.contract: сборка баров, автопостановка после импорта."""

from threading import Event
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from trader_db import read_bars
from trader_db.models import DerivedBuild, Job

from tests.helpers import load
from tests.test_iss_jobs import contract_id_of, run_job
from trader_worker.runner import Worker


def loaded_contract(
    worker: Worker, factory: sessionmaker[Session], root_id: int
) -> int:
    run_job(worker, factory, "iss.sync_root", root_id=root_id, enqueue_imports=False)
    contract_id = contract_id_of(factory, "BRZ6")
    run_job(worker, factory, "import.iss", contract_id=contract_id)
    return contract_id


def jobs_of_type(factory: sessionmaker[Session], job_type: str) -> list[Job]:
    with factory() as session:
        return list(session.scalars(select(Job).where(Job.type == job_type)))


def test_import_enqueues_one_aggregation_and_job_builds_all_timeframes(
    iss_worker: Worker, session_factory: sessionmaker[Session], root_id: int
) -> None:
    contract_id = loaded_contract(iss_worker, session_factory, root_id)

    pending = jobs_of_type(session_factory, "aggregate.contract")
    assert len(pending) == 1  # два куска импорта — одна задача сборки
    assert pending[0].status == "queued"

    iss_worker.run_once(Event())

    job = load(session_factory, pending[0].id)
    assert job.status == "succeeded"
    assert job.result is not None
    builds: list[dict[str, Any]] = job.result["builds"]
    assert [b["timeframe"] for b in builds] == ["15m", "1h", "4h", "1d", "1w"]
    assert job.result["rolls"] == 0  # один контракт — роллов нет
    assert {b["mode"] for b in builds} == {"full"}
    written = {b["timeframe"]: b["rows_written"] for b in builds}
    assert written["15m"] > 0 and written["1d"] > 0
    assert all(b["skipped_outside_session"] == 0 for b in builds)
    with session_factory() as session:
        assert read_bars(session, contract_id, "1d")


def test_repeated_run_is_noop(
    iss_worker: Worker, session_factory: sessionmaker[Session], root_id: int
) -> None:
    contract_id = loaded_contract(iss_worker, session_factory, root_id)
    iss_worker.run_once(Event())

    job = run_job(
        iss_worker, session_factory, "aggregate.contract", contract_id=contract_id
    )

    assert job.status == "succeeded"
    assert {b["mode"] for b in job.result["builds"]} == {"noop"}
    with session_factory() as session:
        builds = session.scalar(select(func.count()).select_from(DerivedBuild))
    assert builds == 5  # noop записей о сборке не создаёт


def test_timeframes_param_limits_the_build(
    iss_worker: Worker, session_factory: sessionmaker[Session], root_id: int
) -> None:
    contract_id = loaded_contract(iss_worker, session_factory, root_id)
    iss_worker.run_once(Event())  # автозадача

    job = run_job(
        iss_worker,
        session_factory,
        "aggregate.contract",
        contract_id=contract_id,
        timeframes=["1d"],
        force=True,
    )

    assert job.status == "succeeded"
    assert [(b["timeframe"], b["mode"]) for b in job.result["builds"]] == [
        ("1d", "full")
    ]


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({}, "contract_id"),
        ({"contract_id": True}, "contract_id"),
        ({"contract_id": 999_999}, "не найден"),
    ],
)
def test_invalid_params_fail_with_a_readable_message(
    iss_worker: Worker,
    session_factory: sessionmaker[Session],
    params: dict[str, Any],
    message: str,
) -> None:
    job = run_job(iss_worker, session_factory, "aggregate.contract", **params)

    assert job.status == "failed"
    assert message in (job.error or "")


def test_unknown_timeframe_and_missing_candles_fail(
    iss_worker: Worker, session_factory: sessionmaker[Session], root_id: int
) -> None:
    run_job(
        iss_worker,
        session_factory,
        "iss.sync_root",
        root_id=root_id,
        enqueue_imports=False,
    )
    contract_id = contract_id_of(session_factory, "BRZ6")

    empty = run_job(
        iss_worker, session_factory, "aggregate.contract", contract_id=contract_id
    )
    bad = run_job(
        iss_worker,
        session_factory,
        "aggregate.contract",
        contract_id=contract_id,
        timeframes=["7m"],
    )

    assert empty.status == "failed"
    assert "нет минутных данных" in (empty.error or "")
    assert bad.status == "failed"
    assert "7m" in (bad.error or "")
