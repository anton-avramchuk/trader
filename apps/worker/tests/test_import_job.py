"""Задача импорта в worker: конвейер под управлением очереди (TRADER_DATABASE_URL)."""

import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Event
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from trader_db import request_cancel, start_import
from trader_db.models import (
    DataImport,
    DatasetVersion,
    RawCandle1m,
)
from trader_engine.ingest import RawRow, RowError

from tests.helpers import enqueue, in_thread, load
from trader_worker.handlers import HandlerRegistry, JobContext
from trader_worker.import_jobs import ImportSource, register_import_job
from trader_worker.runner import Worker

DAY = datetime(2026, 9, 29, 4, 0, tzinfo=UTC)
MINUTE = timedelta(minutes=1)


def rows(count: int) -> Iterator[RawRow | RowError]:
    for i in range(count):
        yield RawRow(
            row_number=i + 1,
            timestamp=DAY + i * MINUTE,
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("100.5"),
            volume=Decimal("10"),
        )


def total(factory: sessionmaker[Session], model: type) -> int:
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def test_import_runs_as_a_job_and_returns_the_report(
    worker: Worker,
    registry: HandlerRegistry,
    session_factory: sessionmaker[Session],
    contract_id: int,
) -> None:
    register_import_job(
        registry,
        "import.synthetic",
        session_factory,
        lambda ctx: ImportSource(
            "csv", "file", rows(int(ctx.params["rows"])), source_name="synthetic"
        ),
    )
    job_id = enqueue(
        session_factory, "import.synthetic", {"contract_id": contract_id, "rows": 500}
    )

    worker.run_once(Event())

    job = load(session_factory, job_id)
    assert job.status == "succeeded"
    assert job.result is not None
    assert job.result["report"]["inserted"] == 500
    assert job.result["dataset_version_id"] is not None
    assert total(session_factory, RawCandle1m) == 500
    assert total(session_factory, DatasetVersion) == 1
    with session_factory() as session:
        data_import = session.get(DataImport, job.result["import_id"])
        assert data_import is not None
        assert data_import.params["job_id"] == job_id
        assert (data_import.status, data_import.source_name) == (
            "completed",
            "synthetic",
        )


def test_cancelling_the_job_rolls_the_whole_import_back(
    worker: Worker,
    registry: HandlerRegistry,
    session_factory: sessionmaker[Session],
    contract_id: int,
) -> None:
    started, release = Event(), Event()

    def blocking_source(context: JobContext) -> ImportSource:
        def generate() -> Iterator[RawRow | RowError]:
            yield from rows(50)
            started.set()
            release.wait(10)
            # Ждём, пока признак отмены дойдёт до задачи через heartbeat.
            deadline = time.monotonic() + 5
            while not context.cancel_requested.is_set() and time.monotonic() < deadline:
                time.sleep(0.02)
            yield from rows(50)

        return ImportSource("csv", "file", generate())

    register_import_job(registry, "import.blocking", session_factory, blocking_source)
    job_id = enqueue(session_factory, "import.blocking", {"contract_id": contract_id})
    thread = in_thread(worker.run_once, Event())
    assert started.wait(5)

    with session_factory() as session:
        request_cancel(session, job_id)
        session.commit()
    release.set()
    thread.join(10)

    assert load(session_factory, job_id).status == "cancelled"
    assert total(session_factory, RawCandle1m) == 0
    assert total(session_factory, DatasetVersion) == 0
    with session_factory() as session:
        (data_import,) = session.scalars(select(DataImport)).all()
    assert data_import.status == "failed"


def test_broken_source_fails_the_job_and_leaves_no_data(
    worker: Worker,
    registry: HandlerRegistry,
    session_factory: sessionmaker[Session],
    contract_id: int,
) -> None:
    def broken(_context: JobContext) -> ImportSource:
        def generate() -> Iterator[RawRow | RowError]:
            yield from rows(10)
            raise OSError("файл недоступен")

        return ImportSource("csv", "file", generate())

    register_import_job(registry, "import.broken", session_factory, broken)
    job_id = enqueue(session_factory, "import.broken", {"contract_id": contract_id})

    worker.run_once(Event())

    job = load(session_factory, job_id)
    assert job.status == "failed"
    assert job.error is not None
    assert "OSError" in job.error
    assert total(session_factory, RawCandle1m) == 0


def test_unknown_contract_fails_the_job(
    worker: Worker,
    registry: HandlerRegistry,
    session_factory: sessionmaker[Session],
) -> None:
    register_import_job(
        registry,
        "import.synthetic",
        session_factory,
        lambda _ctx: ImportSource("csv", "file", rows(1)),
    )
    job_id = enqueue(session_factory, "import.synthetic", {"contract_id": 999_999})

    worker.run_once(Event())

    job = load(session_factory, job_id)
    assert job.status == "failed"
    assert job.error is not None
    assert "999999" in job.error


def test_rerun_of_a_job_abandons_the_import_left_by_the_crashed_attempt(
    worker: Worker,
    registry: HandlerRegistry,
    session_factory: sessionmaker[Session],
    contract_id: int,
) -> None:
    register_import_job(
        registry,
        "import.synthetic",
        session_factory,
        lambda _ctx: ImportSource("csv", "file", rows(20)),
    )
    job_id = enqueue(session_factory, "import.synthetic", {"contract_id": contract_id})
    with session_factory() as session:  # запись прежней попытки, worker упал
        orphan = start_import(
            session,
            provider_code="csv",
            contract_id=contract_id,
            source_type="file",
            params={"job_id": job_id},
        )
        session.commit()

    worker.run_once(Event())

    with session_factory() as session:
        old = session.get(DataImport, orphan)
        assert old is not None
        assert (old.status, old.error) == ("failed", "прерван: задача перезапущена")
    result: dict[str, Any] | None = load(session_factory, job_id).result
    assert result is not None
    assert result["report"]["inserted"] == 20
