"""Интеграционные тесты очереди задач и расписаний (нужен TRADER_DATABASE_URL)."""

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from trader_db import (
    cancel_running_job,
    claim_next_job,
    complete_job,
    create_schedule,
    enqueue_job,
    fail_job,
    get_job,
    make_engine,
    release_job,
    request_cancel,
    requeue_stale_jobs,
    run_due_schedules,
    touch_job,
    upgrade_head,
)
from trader_db.models import Job, JobSchedule

FUTURE = datetime.now(UTC) + timedelta(hours=1)


def age_heartbeat(session: Session, job_id: int, seconds: int) -> None:
    session.execute(
        text(
            "UPDATE jobs SET heartbeat_at = now() - make_interval(secs => :s) "
            "WHERE id = :id"
        ),
        {"s": seconds, "id": job_id},
    )


class TestClaim:
    def test_claims_oldest_ready_job_and_marks_it_running(
        self, session: Session
    ) -> None:
        first = enqueue_job(session, "a", {"x": 1})
        enqueue_job(session, "a")

        job = claim_next_job(session, "w1")

        assert job is not None
        assert (job.id, job.status, job.locked_by, job.attempts) == (
            first,
            "running",
            "w1",
            1,
        )
        assert job.params == {"x": 1}
        assert job.heartbeat_at is not None
        assert job.started_at is not None

    def test_empty_queue_gives_none(self, session: Session) -> None:
        assert claim_next_job(session, "w1") is None

    def test_claimed_job_is_not_claimed_again(self, session: Session) -> None:
        enqueue_job(session, "a")
        claim_next_job(session, "w1")

        assert claim_next_job(session, "w2") is None

    def test_delayed_job_waits_for_run_after(self, session: Session) -> None:
        enqueue_job(session, "a", run_after=FUTURE)

        assert claim_next_job(session, "w1") is None

    def test_type_filter(self, session: Session) -> None:
        enqueue_job(session, "import")
        wanted = enqueue_job(session, "reconcile")

        job = claim_next_job(session, "w1", types=["reconcile"])

        assert job is not None
        assert job.id == wanted

    def test_concurrent_workers_never_share_a_job(self, temp_database_url: str) -> None:
        upgrade_head(temp_database_url)
        engine = make_engine(temp_database_url)
        factory = sessionmaker(engine)
        with factory() as setup:
            ids = {enqueue_job(setup, "a") for _ in range(40)}
            setup.commit()

        claimed: list[int] = []
        lock = threading.Lock()
        errors: list[BaseException] = []

        def worker(name: str) -> None:
            try:
                while True:
                    with factory() as session:
                        job = claim_next_job(session, name)
                        job_id = None if job is None else job.id
                        session.commit()
                    if job_id is None:
                        return
                    with lock:
                        claimed.append(job_id)
            except BaseException as error:  # noqa: BLE001 - передаём в основной поток
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        engine.dispose()

        assert errors == []
        assert sorted(claimed) == sorted(ids)


class TestFinish:
    def _running(self, session: Session, worker: str = "w1") -> int:
        job_id = enqueue_job(session, "a")
        claim_next_job(session, worker)
        return job_id

    def test_complete_stores_result_and_full_progress(self, session: Session) -> None:
        job_id = self._running(session)

        assert complete_job(session, job_id, "w1", {"rows": 5}) is True

        job = get_job(session, job_id)
        assert job is not None
        assert (job.status, job.progress, job.result, job.locked_by) == (
            "succeeded",
            1.0,
            {"rows": 5},
            None,
        )
        assert job.finished_at is not None

    def test_fail_stores_error(self, session: Session) -> None:
        job_id = self._running(session)

        assert fail_job(session, job_id, "w1", "boom") is True

        job = get_job(session, job_id)
        assert job is not None
        assert (job.status, job.error) == ("failed", "boom")

    def test_foreign_worker_cannot_finish_the_job(self, session: Session) -> None:
        job_id = self._running(session, "w1")

        assert complete_job(session, job_id, "w2") is False
        assert fail_job(session, job_id, "w2", "x") is False

        job = get_job(session, job_id)
        assert job is not None
        assert job.status == "running"

    def test_finished_job_cannot_be_finished_again(self, session: Session) -> None:
        job_id = self._running(session)
        complete_job(session, job_id, "w1")

        assert fail_job(session, job_id, "w1", "late") is False

    def test_release_returns_job_to_queue_without_spending_an_attempt(
        self, session: Session
    ) -> None:
        job_id = self._running(session)

        assert release_job(session, job_id, "w1") is True

        job = get_job(session, job_id)
        assert job is not None
        assert (job.status, job.attempts, job.locked_by) == ("queued", 0, None)
        assert claim_next_job(session, "w2") is not None


class TestTouch:
    def test_touch_updates_progress_and_heartbeat(self, session: Session) -> None:
        job_id = enqueue_job(session, "a")
        claim_next_job(session, "w1")
        age_heartbeat(session, job_id, 60)

        cancel = touch_job(session, job_id, "w1", progress=0.4, message="half")

        job = get_job(session, job_id)
        assert cancel is False
        assert job is not None
        assert (job.progress, job.progress_message) == (0.4, "half")
        assert job.heartbeat_at is not None
        assert datetime.now(UTC) - job.heartbeat_at < timedelta(seconds=30)

    def test_touch_without_progress_keeps_it(self, session: Session) -> None:
        job_id = enqueue_job(session, "a")
        claim_next_job(session, "w1")
        touch_job(session, job_id, "w1", progress=0.4, message="half")

        touch_job(session, job_id, "w1")

        job = get_job(session, job_id)
        assert job is not None
        assert (job.progress, job.progress_message) == (0.4, "half")

    def test_progress_is_clamped_to_unit_interval(self, session: Session) -> None:
        job_id = enqueue_job(session, "a")
        claim_next_job(session, "w1")

        touch_job(session, job_id, "w1", progress=7)

        job = get_job(session, job_id)
        assert job is not None
        assert job.progress == 1.0

    def test_touch_by_foreign_worker_reports_lost_ownership(
        self, session: Session
    ) -> None:
        job_id = enqueue_job(session, "a")
        claim_next_job(session, "w1")

        assert touch_job(session, job_id, "w2") is None


class TestCancel:
    def test_queued_job_is_cancelled_immediately(self, session: Session) -> None:
        job_id = enqueue_job(session, "a")

        job = request_cancel(session, job_id)

        assert job is not None
        assert (job.status, job.cancel_requested) == ("cancelled", False)
        assert job.finished_at is not None
        assert claim_next_job(session, "w1") is None

    def test_running_job_gets_a_flag_and_stays_running(self, session: Session) -> None:
        job_id = enqueue_job(session, "a")
        claim_next_job(session, "w1")

        job = request_cancel(session, job_id)

        assert job is not None
        assert (job.status, job.cancel_requested) == ("running", True)
        assert touch_job(session, job_id, "w1") is True

    def test_worker_confirms_cancellation(self, session: Session) -> None:
        job_id = enqueue_job(session, "a")
        claim_next_job(session, "w1")
        request_cancel(session, job_id)

        assert cancel_running_job(session, job_id, "w1") is True

        job = get_job(session, job_id)
        assert job is not None
        assert job.status == "cancelled"

    def test_finished_or_unknown_job_cannot_be_cancelled(
        self, session: Session
    ) -> None:
        job_id = enqueue_job(session, "a")
        claim_next_job(session, "w1")
        complete_job(session, job_id, "w1")

        assert request_cancel(session, job_id) is None
        assert request_cancel(session, 999_999) is None


class TestStaleRecovery:
    def test_stale_running_job_is_requeued_while_attempts_remain(
        self, session: Session
    ) -> None:
        job_id = enqueue_job(session, "a", max_attempts=2)
        claim_next_job(session, "dead-worker")
        age_heartbeat(session, job_id, 120)

        assert requeue_stale_jobs(session, 30) == (1, 0)

        job = get_job(session, job_id)
        assert job is not None
        assert (job.status, job.locked_by, job.attempts) == ("queued", None, 1)
        assert job.error is not None
        again = claim_next_job(session, "w2")
        assert again is not None
        assert (again.id, again.attempts) == (job_id, 2)
        assert complete_job(session, job_id, "w2") is True
        finished = get_job(session, job_id)
        assert finished is not None
        assert (finished.status, finished.error) == ("succeeded", None)

    def test_stale_job_fails_when_attempts_are_exhausted(
        self, session: Session
    ) -> None:
        job_id = enqueue_job(session, "a", max_attempts=1)
        claim_next_job(session, "dead-worker")
        age_heartbeat(session, job_id, 120)

        assert requeue_stale_jobs(session, 30) == (0, 1)

        job = get_job(session, job_id)
        assert job is not None
        assert job.status == "failed"
        assert job.finished_at is not None

    def test_fresh_job_is_left_alone(self, session: Session) -> None:
        job_id = enqueue_job(session, "a")
        claim_next_job(session, "w1")

        assert requeue_stale_jobs(session, 30) == (0, 0)

        job = get_job(session, job_id)
        assert job is not None
        assert job.status == "running"

    def test_previous_owner_loses_the_job_after_requeue(self, session: Session) -> None:
        job_id = enqueue_job(session, "a")
        claim_next_job(session, "old")
        age_heartbeat(session, job_id, 120)
        requeue_stale_jobs(session, 30)
        claim_next_job(session, "new")

        assert touch_job(session, job_id, "old") is None
        assert complete_job(session, job_id, "old") is False


class TestConstraints:
    def test_progress_must_be_within_unit_interval(self, session: Session) -> None:
        session.add(Job(type="a", progress=2.0))

        with pytest.raises(IntegrityError):
            session.flush()

    def test_unknown_status_is_rejected(self, session: Session) -> None:
        session.add(Job(type="a", status="paused"))

        with pytest.raises(IntegrityError):
            session.flush()


class TestSchedules:
    def test_due_schedule_enqueues_one_job_and_moves_forward(
        self, session: Session
    ) -> None:
        schedule_id = create_schedule(
            session, "nightly", "reconcile", 3600, params={"days": 1}
        )

        created = run_due_schedules(session)

        assert len(created) == 1
        job = get_job(session, created[0])
        assert job is not None
        assert (job.type, job.params, job.schedule_id) == (
            "reconcile",
            {"days": 1},
            schedule_id,
        )
        schedule = session.get(JobSchedule, schedule_id)
        assert schedule is not None
        session.refresh(schedule)
        assert schedule.next_run_at > datetime.now(UTC)
        assert run_due_schedules(session) == []

    def test_not_yet_due_schedule_is_skipped(self, session: Session) -> None:
        create_schedule(session, "later", "a", 60, first_run_at=FUTURE)

        assert run_due_schedules(session) == []

    def test_disabled_schedule_is_skipped(self, session: Session) -> None:
        create_schedule(session, "off", "a", 60, enabled=False)

        assert run_due_schedules(session) == []

    def test_overlapping_run_is_skipped_but_schedule_still_advances(
        self, session: Session
    ) -> None:
        schedule_id = create_schedule(session, "busy", "a", 3600)
        first = run_due_schedules(session)
        assert len(first) == 1
        session.execute(
            text("UPDATE job_schedules SET next_run_at = now() WHERE id = :id"),
            {"id": schedule_id},
        )

        assert run_due_schedules(session) == []

        schedule = session.get(JobSchedule, schedule_id)
        assert schedule is not None
        session.refresh(schedule)
        assert schedule.next_run_at > datetime.now(UTC)
        assert session.scalars(select(Job.id)).all() == first

    def test_new_run_is_allowed_after_previous_finished(self, session: Session) -> None:
        schedule_id = create_schedule(session, "seq", "a", 3600)
        first = run_due_schedules(session)
        claim_next_job(session, "w1")
        complete_job(session, first[0], "w1")
        session.execute(
            text("UPDATE job_schedules SET next_run_at = now() WHERE id = :id"),
            {"id": schedule_id},
        )

        assert len(run_due_schedules(session)) == 1

    def test_missed_intervals_do_not_pile_up(self, session: Session) -> None:
        first_run = datetime.now(UTC) - timedelta(hours=10, minutes=5)
        schedule_id = create_schedule(
            session, "missed", "a", 3600, first_run_at=first_run
        )

        created = run_due_schedules(session)

        assert len(created) == 1
        schedule = session.get(JobSchedule, schedule_id)
        assert schedule is not None
        session.refresh(schedule)
        now = datetime.now(UTC)
        assert now < schedule.next_run_at <= now + timedelta(hours=1)
        # Сетка запусков сохранена: сдвиг от исходного времени кратен интервалу.
        assert (schedule.next_run_at - first_run) % timedelta(hours=1) == timedelta(0)

    def test_interval_must_be_positive(self, session: Session) -> None:
        with pytest.raises(IntegrityError):
            create_schedule(session, "bad", "a", 0)
