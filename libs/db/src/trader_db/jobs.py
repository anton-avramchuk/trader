"""Очередь задач на PostgreSQL: постановка, захват, heartbeat, завершение, отмена.

Захват — ``SELECT … FOR UPDATE SKIP LOCKED``: несколько worker'ов не берут одну
задачу. Все операции владельца (heartbeat, завершение, освобождение) проверяют
``locked_by``: потерявший задачу worker не может её перезаписать. Время берётся
из ``now()`` БД. Функции только выполняют SQL и делают ``flush`` — фиксацию
транзакции выполняет вызывающий.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Executable,
    Float,
    bindparam,
    case,
    exists,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.orm import Session

from trader_db.models import Job

ACTIVE_STATUSES = ("queued", "running")
_NO_SYNC = {"synchronize_session": False}


def enqueue_statement(
    job_type: str,
    params: dict[str, Any] | None = None,
    *,
    run_after: datetime | None = None,
    max_attempts: int = 3,
    schedule_id: int | None = None,
) -> Executable:
    """INSERT новой задачи с ``RETURNING Job`` (для sync и async сессий)."""
    values: dict[str, Any] = {
        "type": job_type,
        "params": params or {},
        "max_attempts": max_attempts,
        "schedule_id": schedule_id,
    }
    if run_after is not None:
        values["run_after"] = run_after
    return insert(Job).values(**values).returning(Job)


def has_active_contract_job(session: Session, job_type: str, contract_id: int) -> bool:
    """Есть ли ожидающая или выполняющаяся задача типа ``job_type`` по контракту."""
    return bool(
        session.scalar(
            select(
                exists().where(
                    Job.type == job_type,
                    Job.params["contract_id"].as_integer() == contract_id,
                    Job.status.in_(ACTIVE_STATUSES),
                )
            )
        )
    )


def cancel_statement(job_id: int) -> Executable:
    """Отмена: ожидающая задача отменяется сразу, выполняющая получает флаг.

    Возвращает строку задачи; пусто — задачи нет или она уже завершена.
    """
    queued = Job.status == "queued"
    return (
        update(Job)
        .where(Job.id == job_id, Job.status.in_(ACTIVE_STATUSES))
        .values(
            status=case((queued, "cancelled"), else_=Job.status),
            cancel_requested=case((queued, Job.cancel_requested), else_=True),
            finished_at=case((queued, func.now()), else_=Job.finished_at),
            updated_at=func.now(),
        )
        .returning(Job)
        .execution_options(**_NO_SYNC)
    )


def get_job_statement(job_id: int) -> Executable:
    return select(Job).where(Job.id == job_id).execution_options(populate_existing=True)


def list_jobs_statement(
    *, status: str | None = None, job_type: str | None = None, limit: int = 50
) -> Executable:
    query = select(Job).order_by(Job.id.desc()).limit(limit)
    if status is not None:
        query = query.where(Job.status == status)
    if job_type is not None:
        query = query.where(Job.type == job_type)
    return query.execution_options(populate_existing=True)


def enqueue_job(
    session: Session,
    job_type: str,
    params: dict[str, Any] | None = None,
    *,
    run_after: datetime | None = None,
    max_attempts: int = 3,
    schedule_id: int | None = None,
) -> int:
    job = session.scalars(
        enqueue_statement(
            job_type,
            params,
            run_after=run_after,
            max_attempts=max_attempts,
            schedule_id=schedule_id,
        )
    ).one()
    session.flush()
    return job.id


def get_job(session: Session, job_id: int) -> Job | None:
    return session.scalars(get_job_statement(job_id)).one_or_none()


def request_cancel(session: Session, job_id: int) -> Job | None:
    """Запросить отмену; ``None``, если задачи нет или она уже завершена."""
    return session.scalars(cancel_statement(job_id)).one_or_none()


def claim_next_job(
    session: Session, worker_id: str, *, types: Sequence[str] | None = None
) -> Job | None:
    """Взять самую раннюю готовую задачу; ``None``, если очередь пуста."""
    query = select(Job).where(Job.status == "queued", Job.run_after <= func.now())
    if types is not None:
        query = query.where(Job.type.in_(types))
    job = session.scalars(
        query.order_by(Job.id).limit(1).with_for_update(skip_locked=True)
    ).first()
    if job is None:
        return None
    job.status = "running"
    job.locked_by = worker_id
    job.attempts += 1
    job.heartbeat_at = func.now()
    job.started_at = func.now()
    job.updated_at = func.now()
    session.flush()
    session.refresh(job)
    return job


def touch_job(
    session: Session,
    job_id: int,
    worker_id: str,
    *,
    progress: float | None = None,
    message: str | None = None,
) -> bool | None:
    """Heartbeat и (опционально) прогресс.

    Возвращает ``cancel_requested``; ``None`` — задача больше не принадлежит
    этому worker'у (возвращена в очередь, отменена или завершена).
    """
    values: dict[str, Any] = {"heartbeat_at": func.now(), "updated_at": func.now()}
    if progress is not None:
        values["progress"] = min(1.0, max(0.0, progress))
    if message is not None:
        values["progress_message"] = message
    result = session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == "running", Job.locked_by == worker_id)
        .values(**values)
        .returning(Job.cancel_requested)
        .execution_options(**_NO_SYNC)
    ).first()
    return None if result is None else bool(result[0])


def _finish(
    session: Session,
    job_id: int,
    worker_id: str,
    **values: Any,
) -> bool:
    result = session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == "running", Job.locked_by == worker_id)
        .values(
            finished_at=func.now(),
            updated_at=func.now(),
            locked_by=None,
            **values,
        )
        .returning(Job.id)
        .execution_options(**_NO_SYNC)
    ).first()
    return result is not None


def complete_job(
    session: Session,
    job_id: int,
    worker_id: str,
    result: dict[str, Any] | None = None,
) -> bool:
    return _finish(
        session,
        job_id,
        worker_id,
        status="succeeded",
        progress=1.0,
        result=result,
        error=None,
    )


def fail_job(session: Session, job_id: int, worker_id: str, error: str) -> bool:
    return _finish(session, job_id, worker_id, status="failed", error=error)


def cancel_running_job(session: Session, job_id: int, worker_id: str) -> bool:
    """Worker подтверждает остановку по запросу отмены."""
    return _finish(session, job_id, worker_id, status="cancelled")


def release_job(session: Session, job_id: int, worker_id: str) -> bool:
    """Вернуть задачу в очередь (штатная остановка worker'а), не тратя попытку."""
    result = session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == "running", Job.locked_by == worker_id)
        .values(
            status="queued",
            locked_by=None,
            heartbeat_at=None,
            attempts=Job.attempts - 1,
            run_after=func.now(),
            updated_at=func.now(),
        )
        .returning(Job.id)
        .execution_options(**_NO_SYNC)
    ).first()
    return result is not None


def requeue_stale_jobs(session: Session, stale_after_seconds: float) -> tuple[int, int]:
    """Вернуть зависшие задачи в очередь; вернуть ``(возвращено, провалено)``.

    Зависшая — ``running`` без heartbeat дольше ``stale_after_seconds``. Пока
    попытки остались, задача снова ``queued``; иначе — ``failed``.
    """
    threshold = func.now() - func.make_interval(
        0, 0, 0, 0, 0, 0, bindparam("secs", stale_after_seconds, type_=Float)
    )
    stale = (Job.status == "running") & (Job.heartbeat_at < threshold)
    requeued = session.execute(
        update(Job)
        .where(stale, Job.attempts < Job.max_attempts)
        .values(
            status="queued",
            locked_by=None,
            heartbeat_at=None,
            run_after=func.now(),
            updated_at=func.now(),
            error="worker потерян: задача возвращена в очередь",
        )
        .returning(Job.id)
        .execution_options(**_NO_SYNC)
    ).all()
    failed = session.execute(
        update(Job)
        .where(stale, Job.attempts >= Job.max_attempts)
        .values(
            status="failed",
            locked_by=None,
            finished_at=func.now(),
            updated_at=func.now(),
            error="worker потерян: попытки исчерпаны",
        )
        .returning(Job.id)
        .execution_options(**_NO_SYNC)
    ).all()
    return len(requeued), len(failed)
