"""Простейший интервальный планировщик: расписания порождают задачи очереди."""

from datetime import datetime
from typing import Any

from sqlalchemy import exists, func, select, text
from sqlalchemy.orm import Session

from trader_db.jobs import ACTIVE_STATUSES, enqueue_job
from trader_db.models import Job, JobSchedule


def create_schedule(
    session: Session,
    name: str,
    job_type: str,
    interval_seconds: int,
    *,
    params: dict[str, Any] | None = None,
    first_run_at: datetime | None = None,
    enabled: bool = True,
) -> int:
    """Создать расписание; первый запуск — ``first_run_at`` (по умолчанию сейчас)."""
    schedule = JobSchedule(
        name=name,
        job_type=job_type,
        interval_seconds=interval_seconds,
        params=params or {},
        next_run_at=first_run_at if first_run_at is not None else func.now(),
        enabled=enabled,
    )
    session.add(schedule)
    session.flush()
    return schedule.id


def ensure_schedule(
    session: Session,
    name: str,
    job_type: str,
    interval_seconds: int,
    *,
    params: dict[str, Any] | None = None,
) -> int:
    """Создать расписание ``name``, если его нет; существующее не трогается.

    Так стартовые расписания worker'а можно объявлять при каждом запуске, не
    сбрасывая ``next_run_at`` и не затирая правки пользователя.
    """
    existing = session.scalar(select(JobSchedule.id).where(JobSchedule.name == name))
    if existing is not None:
        return existing
    return create_schedule(session, name, job_type, interval_seconds, params=params)


def run_due_schedules(session: Session) -> list[int]:
    """Поставить задачи по наступившим расписаниям; вернуть id созданных задач.

    Запуск пропускается, если предыдущая задача расписания ещё в очереди или
    выполняется. Следующий запуск всегда сдвигается на ближайший будущий шаг
    интервала, поэтому после простоя пропущенные запуски не накапливаются.
    """
    due = session.scalars(
        select(JobSchedule)
        .where(JobSchedule.enabled, JobSchedule.next_run_at <= func.now())
        .order_by(JobSchedule.next_run_at)
        .with_for_update(skip_locked=True)
    ).all()
    created: list[int] = []
    for schedule in due:
        busy = session.scalar(
            select(
                exists().where(
                    Job.schedule_id == schedule.id, Job.status.in_(ACTIVE_STATUSES)
                )
            )
        )
        if not busy:
            created.append(
                enqueue_job(
                    session,
                    schedule.job_type,
                    schedule.params,
                    schedule_id=schedule.id,
                )
            )
        session.execute(
            text(
                "UPDATE job_schedules SET next_run_at = next_run_at + "
                "make_interval(secs => interval_seconds * "
                "(floor(extract(epoch FROM (now() - next_run_at)) "
                "/ interval_seconds) + 1)) WHERE id = :id"
            ),
            {"id": schedule.id},
        )
    session.flush()
    return created
