"""Прогоны движков и их неизменяемый лог событий (ADR-0003, ADR-0021)."""

import datetime as dt
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from trader_db.models.base import Base, CreatedAtMixin


class EngineRun(CreatedAtMixin, Base):
    """Прогон движка по ряду свечей инструмента на таймфрейме.

    Ключ прогона — движок, версия алгоритма, отпечаток параметров, ряд и TF.
    Прогон можно продолжить на новых барах, если ранее обработанные бары не
    изменились (``input_fingerprint``); иначе начинается новый прогон, а старый
    остаётся как есть. Строка прогона хранит лишь служебные счётчики и
    состояние движка — сами события неизменяемы.
    """

    __tablename__ = "engine_runs"
    __table_args__ = (
        CheckConstraint("bars_processed >= 0", name="bars_non_negative"),
        Index(
            "ix_engine_runs_key",
            "engine",
            "params_hash",
            "algorithm_version",
            "timeframe_code",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    engine: Mapped[str] = mapped_column(String(64))
    algorithm_version: Mapped[int] = mapped_column(Integer)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    params_hash: Mapped[str] = mapped_column(String(64))
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE")
    )
    timeframe_code: Mapped[str] = mapped_column(
        ForeignKey("timeframes.code", ondelete="RESTRICT")
    )
    bars_processed: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    last_close_time: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # Цепочный отпечаток обработанных баров и состояние движка для продолжения.
    input_fingerprint: Mapped[str] = mapped_column(Text, server_default=text("''"))
    state: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EngineEvent(Base):
    """Событие движка. Строки не изменяются и не удаляются (триггер в БД).

    ``revises_seq`` — событие той же цепочки, которое это событие пересматривает
    или отменяет. ``available_at`` — момент, с которого событие известно.
    """

    __tablename__ = "engine_events"
    __table_args__ = (
        PrimaryKeyConstraint("run_id", "seq", name="pk_engine_events"),
        ForeignKeyConstraint(["run_id"], ["engine_runs.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["run_id", "revises_seq"],
            ["engine_events.run_id", "engine_events.seq"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('detected', 'confirmed', 'revised', 'invalidated')",
            name="status_valid",
        ),
        CheckConstraint(
            "(status IN ('revised', 'invalidated')) = (revises_seq IS NOT NULL)"
            " OR status = 'confirmed'",
            name="revises_matches_status",
        ),
        CheckConstraint("revises_seq < seq", name="revises_earlier"),
        CheckConstraint(
            "detected_at <= available_at", name="detected_before_available"
        ),
        CheckConstraint(
            "confirmed_at IS NULL OR "
            "(confirmed_at >= detected_at AND confirmed_at <= available_at)",
            name="confirmed_in_range",
        ),
        Index("ix_engine_events_available", "run_id", "available_at"),
    )

    run_id: Mapped[int] = mapped_column(BigInteger)
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    detected_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    revises_seq: Mapped[int | None] = mapped_column(Integer)
