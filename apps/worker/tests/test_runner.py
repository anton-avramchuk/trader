"""Интеграционные тесты worker'а на временной БД (нужен TRADER_DATABASE_URL)."""

from threading import Event
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from trader_db import (
    claim_next_job,
    create_schedule,
    enqueue_job,
    request_cancel,
)

from tests.helpers import enqueue, in_thread, load, wait_until
from trader_worker.handlers import HandlerRegistry, JobContext, default_registry
from trader_worker.runner import Worker


def echo(context: JobContext) -> dict[str, Any]:
    return dict(context.params)


class TestExecution:
    def test_runs_handler_and_stores_result(
        self,
        worker: Worker,
        registry: HandlerRegistry,
        session_factory: sessionmaker[Session],
    ) -> None:
        registry.register("echo")(echo)
        job_id = enqueue(session_factory, "echo", {"a": 1})

        assert worker.run_once(Event()) is True

        job = load(session_factory, job_id)
        assert (job.status, job.result, job.progress, job.attempts) == (
            "succeeded",
            {"a": 1},
            1.0,
            1,
        )

    def test_empty_queue_does_nothing(self, worker: Worker) -> None:
        assert worker.run_once(Event()) is False

    def test_failing_handler_marks_job_failed_with_traceback(
        self,
        worker: Worker,
        registry: HandlerRegistry,
        session_factory: sessionmaker[Session],
    ) -> None:
        @registry.register("boom")
        def boom(context: JobContext) -> None:
            raise ValueError("boom!")

        job_id = enqueue(session_factory, "boom")

        worker.run_once(Event())

        job = load(session_factory, job_id)
        assert job.status == "failed"
        assert job.error is not None
        assert "ValueError: boom!" in job.error

    def test_unknown_job_type_fails_loudly_instead_of_hanging(
        self, worker: Worker, session_factory: sessionmaker[Session]
    ) -> None:
        job_id = enqueue(session_factory, "typo.type")

        worker.run_once(Event())

        job = load(session_factory, job_id)
        assert job.status == "failed"
        assert job.error is not None
        assert "typo.type" in job.error

    def test_result_that_is_not_json_fails_the_job(
        self,
        worker: Worker,
        registry: HandlerRegistry,
        session_factory: sessionmaker[Session],
    ) -> None:
        @registry.register("bad.result")
        def bad(context: JobContext) -> dict[str, Any]:
            return {"x": object()}

        job_id = enqueue(session_factory, "bad.result")

        worker.run_once(Event())

        job = load(session_factory, job_id)
        assert job.status == "failed"
        assert job.error is not None
        assert "сохранить результат" in job.error

    def test_builtin_demo_job_reports_steps(
        self, session_factory: sessionmaker[Session], worker: Worker
    ) -> None:
        demo_worker = Worker(session_factory, default_registry(), worker.config)
        job_id = enqueue(session_factory, "demo.sleep", {"seconds": 0.2, "steps": 4})

        demo_worker.run_once(Event())

        job = load(session_factory, job_id)
        assert (job.status, job.result) == ("succeeded", {"slept": 0.2, "steps": 4})
        assert job.progress_message == "шаг 4 из 4"


class TestWhileRunning:
    def test_progress_is_visible_in_db_while_job_runs(
        self,
        worker: Worker,
        registry: HandlerRegistry,
        session_factory: sessionmaker[Session],
    ) -> None:
        reached, release = Event(), Event()

        @registry.register("blocking")
        def blocking(context: JobContext) -> dict[str, Any]:
            context.report_progress(0.5, "половина")
            reached.set()
            release.wait(10)
            return {"ok": True}

        job_id = enqueue(session_factory, "blocking")
        thread = in_thread(worker.run_once, Event())

        assert reached.wait(5)
        running = load(session_factory, job_id)
        assert (running.status, running.progress, running.progress_message) == (
            "running",
            0.5,
            "половина",
        )
        release.set()
        thread.join(5)
        assert load(session_factory, job_id).status == "succeeded"

    def test_heartbeat_keeps_a_silent_job_alive(
        self,
        worker: Worker,
        registry: HandlerRegistry,
        session_factory: sessionmaker[Session],
    ) -> None:
        started, release = Event(), Event()

        @registry.register("silent")
        def silent(context: JobContext) -> None:
            started.set()
            release.wait(10)

        job_id = enqueue(session_factory, "silent")
        thread = in_thread(worker.run_once, Event())
        assert started.wait(5)
        first = load(session_factory, job_id).heartbeat_at
        assert first is not None

        wait_until(
            lambda: (load(session_factory, job_id).heartbeat_at or first) > first
        )

        release.set()
        thread.join(5)

    def test_cancel_request_stops_a_running_job(
        self,
        worker: Worker,
        registry: HandlerRegistry,
        session_factory: sessionmaker[Session],
    ) -> None:
        started = Event()

        @registry.register("long")
        def long_job(context: JobContext) -> None:
            started.set()
            context.wait(30)

        job_id = enqueue(session_factory, "long")
        thread = in_thread(worker.run_once, Event())
        assert started.wait(5)

        with session_factory() as session:
            request_cancel(session, job_id)
            session.commit()
        thread.join(5)

        job = load(session_factory, job_id)
        assert (job.status, job.cancel_requested) == ("cancelled", True)
        assert not thread.is_alive()

    def test_shutdown_returns_the_job_to_the_queue_without_spending_an_attempt(
        self,
        worker: Worker,
        registry: HandlerRegistry,
        session_factory: sessionmaker[Session],
    ) -> None:
        started, stop = Event(), Event()

        @registry.register("long")
        def long_job(context: JobContext) -> None:
            started.set()
            context.wait(30)

        job_id = enqueue(session_factory, "long")
        thread = in_thread(worker.run_once, stop)
        assert started.wait(5)

        stop.set()
        thread.join(5)

        job = load(session_factory, job_id)
        assert (job.status, job.attempts, job.locked_by) == ("queued", 0, None)

    def test_worker_drops_a_job_it_no_longer_owns(
        self,
        worker: Worker,
        registry: HandlerRegistry,
        session_factory: sessionmaker[Session],
    ) -> None:
        started = Event()

        @registry.register("long")
        def long_job(context: JobContext) -> None:
            started.set()
            context.wait(30)

        job_id = enqueue(session_factory, "long")
        thread = in_thread(worker.run_once, Event())
        assert started.wait(5)

        with session_factory() as session:  # кто-то другой забрал задачу назад
            session.execute(
                text(
                    "UPDATE jobs SET status = 'queued', locked_by = NULL, "
                    "heartbeat_at = NULL WHERE id = :id"
                ),
                {"id": job_id},
            )
            session.commit()
        thread.join(5)

        job = load(session_factory, job_id)
        assert job.status == "queued"
        assert not thread.is_alive()


class TestRecovery:
    def test_job_of_a_crashed_worker_is_not_lost(
        self,
        worker: Worker,
        registry: HandlerRegistry,
        session_factory: sessionmaker[Session],
    ) -> None:
        registry.register("echo")(echo)
        with session_factory() as session:
            job_id = enqueue_job(session, "echo", {"n": 7})
            claim_next_job(session, "crashed-worker")  # взял и «упал»
            session.execute(
                text(
                    "UPDATE jobs SET heartbeat_at = now() - interval '5 minutes' "
                    "WHERE id = :id"
                ),
                {"id": job_id},
            )
            session.commit()

        worker.housekeeping()
        assert worker.run_once(Event()) is True

        job = load(session_factory, job_id)
        assert (job.status, job.result, job.attempts) == ("succeeded", {"n": 7}, 2)
        assert job.error is None  # пометка о потерянном worker не остаётся у успешной

    def test_due_schedule_produces_a_job_that_the_worker_runs(
        self,
        worker: Worker,
        registry: HandlerRegistry,
        session_factory: sessionmaker[Session],
    ) -> None:
        registry.register("echo")(echo)
        with session_factory() as session:
            create_schedule(session, "nightly", "echo", 3600, params={"k": "v"})
            session.commit()

        worker.housekeeping()
        assert worker.run_once(Event()) is True

        with session_factory() as session:
            rows = session.execute(text("SELECT status, result FROM jobs")).all()
        assert [(r.status, r.result) for r in rows] == [("succeeded", {"k": "v"})]


class TestRunLoop:
    def test_loop_drains_the_queue_and_stops_on_signal(
        self,
        worker: Worker,
        registry: HandlerRegistry,
        session_factory: sessionmaker[Session],
    ) -> None:
        registry.register("echo")(echo)
        stop = Event()
        thread = in_thread(worker.run, stop)

        ids = [enqueue(session_factory, "echo", {"i": i}) for i in range(3)]
        wait_until(
            lambda: all(load(session_factory, i).status == "succeeded" for i in ids)
        )
        stop.set()
        thread.join(5)

        assert not thread.is_alive()
        assert [load(session_factory, i).result for i in ids] == [
            {"i": 0},
            {"i": 1},
            {"i": 2},
        ]
