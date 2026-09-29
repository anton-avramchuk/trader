"""Очередь задач на PostgreSQL и интервальное расписание (ADR-0002)."""

import datetime as dt
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from trader_db.models.base import Base, CreatedAtMixin

JOB_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")


class Job(CreatedAtMixin, Base):
    """Задача очереди.

    Жизненный цикл: ``queued`` → ``running`` → ``succeeded`` / ``failed`` /
    ``cancelled``. Зависшая ``running`` задача (нет heartbeat) возвращается
    в ``queued``, пока не исчерпаны ``max_attempts``. Все проверки времени
    выполняются по часам БД (``now()``), а не процессов.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="status_valid",
        ),
        CheckConstraint("progress >= 0 AND progress <= 1", name="progress_range"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        # Быстрый захват: только ожидающие задачи, по порядку.
        Index(
            "ix_jobs_queued",
            "run_after",
            "id",
            postgresql_where=text("status = 'queued'"),
        ),
        Index(
            "ix_jobs_running_heartbeat",
            "heartbeat_at",
            postgresql_where=text("status = 'running'"),
        ),
        Index("ix_jobs_schedule_id", "schedule_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    type: Mapped[str] = mapped_column(String(64))
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(16), server_default="queued")
    progress: Mapped[float] = mapped_column(Float, server_default="0")
    progress_message: Mapped[str | None] = mapped_column(String(256))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, server_default="3")
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false")
    )
    locked_by: Mapped[str | None] = mapped_column(String(128))
    run_after: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    heartbeat_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_schedules.id", ondelete="SET NULL")
    )


class JobSchedule(CreatedAtMixin, Base):
    """Периодический запуск: каждые ``interval_seconds`` от ``next_run_at``.

    Простейший планировщик без cron: пропущенные запуски не накапливаются,
    новый запуск не создаётся, пока предыдущая задача расписания активна.
    """

    __tablename__ = "job_schedules"
    __table_args__ = (
        CheckConstraint("interval_seconds > 0", name="interval_positive"),
        Index(
            "ix_job_schedules_due",
            "next_run_at",
            postgresql_where=text("enabled"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    job_type: Mapped[str] = mapped_column(String(64))
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    interval_seconds: Mapped[int] = mapped_column(Integer)
    next_run_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=true())
