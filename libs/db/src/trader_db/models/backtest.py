"""Бэктесты: эксперименты, сделки, окна, журнал и блокировки test (ADR-0027)."""

import datetime as dt
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from trader_db.models.base import Base, CreatedAtMixin


class BacktestExperiment(CreatedAtMixin, Base):
    """Запуск бэктеста по continuous-серии root: все входы и версии для воспроизведения.

    ``family`` — семейство стратегии (источник сигнала): по связке (root, TF, family)
    блокируется test-период. ``versions`` фиксирует версии движков и шаблона,
    ``dataset_version_id`` — версию данных; при тех же версиях сделки совпадают.
    """

    __tablename__ = "backtest_experiments"
    __table_args__ = (
        CheckConstraint("kind IN ('single', 'walk_forward')", name="kind"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')", name="status"
        ),
        CheckConstraint("period_from <= period_to", name="period_order"),
        Index("ix_backtest_experiments_series", "root_id", "timeframe_code"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default="queued")
    root_id: Mapped[int] = mapped_column(ForeignKey("roots.id", ondelete="CASCADE"))
    timeframe_code: Mapped[str] = mapped_column(
        ForeignKey("timeframes.code", ondelete="RESTRICT")
    )
    family: Mapped[str] = mapped_column(String(64))
    strategy: Mapped[dict[str, Any]] = mapped_column(JSONB)
    costs: Mapped[dict[str, Any]] = mapped_column(JSONB)
    contracts: Mapped[int] = mapped_column(Integer, server_default="1")
    period_from: Mapped[dt.date] = mapped_column(Date)
    period_to: Mapped[dt.date] = mapped_column(Date)
    test_from: Mapped[dt.date | None] = mapped_column(Date)
    test_to: Mapped[dt.date | None] = mapped_column(Date)
    walk_forward: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    dataset_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="RESTRICT")
    )
    versions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    params_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL")
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class BacktestWindow(Base):
    """Окно walk-forward: периоды, выбранные параметры и метрики train/validation."""

    __tablename__ = "backtest_windows"
    __table_args__ = (
        UniqueConstraint("experiment_id", "sequence"),
        CheckConstraint("train_from <= train_to", name="train_order"),
        CheckConstraint("valid_from <= valid_to", name="valid_order"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_experiments.id", ondelete="CASCADE")
    )
    sequence: Mapped[int] = mapped_column(Integer)
    train_from: Mapped[dt.date] = mapped_column(Date)
    train_to: Mapped[dt.date] = mapped_column(Date)
    valid_from: Mapped[dt.date] = mapped_column(Date)
    valid_to: Mapped[dt.date] = mapped_column(Date)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    train_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    valid_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class BacktestTrade(Base):
    """Сделка эксперимента (в тиках на контракт, пунктах цены и ₽)."""

    __tablename__ = "backtest_trades"
    __table_args__ = (
        UniqueConstraint("experiment_id", "segment", "sequence"),
        CheckConstraint("side IN ('long', 'short')", name="side"),
        CheckConstraint("segment IN ('single', 'validation', 'test')", name="segment"),
        Index("ix_backtest_trades_window", "window_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_experiments.id", ondelete="CASCADE")
    )
    window_id: Mapped[int | None] = mapped_column(
        ForeignKey("backtest_windows.id", ondelete="CASCADE")
    )
    segment: Mapped[str] = mapped_column(String(16), server_default="single")
    sequence: Mapped[int] = mapped_column(Integer)
    side: Mapped[str] = mapped_column(String(8))
    ref: Mapped[str | None] = mapped_column(String(128))
    contracts: Mapped[int] = mapped_column(Integer)
    entry_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    exit_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(16))
    legs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    gross_ticks: Mapped[float] = mapped_column(Float)
    cost_ticks: Mapped[float] = mapped_column(Float)
    mfe_ticks: Mapped[float] = mapped_column(Float)
    mae_ticks: Mapped[float] = mapped_column(Float)
    gross_points: Mapped[float] = mapped_column(Float)
    net_points: Mapped[float] = mapped_column(Float)
    commission_rub: Mapped[float] = mapped_column(Float)
    gross_rub: Mapped[float | None] = mapped_column(Float)
    net_rub: Mapped[float | None] = mapped_column(Float)
    step_price_estimated: Mapped[bool] = mapped_column(Boolean, server_default="false")
    ambiguous_bar: Mapped[bool] = mapped_column(Boolean, server_default="false")
    rolled: Mapped[bool] = mapped_column(Boolean, server_default="false")


class BacktestLog(CreatedAtMixin, Base):
    """Журнал всех запусков и действий с test-периодом (множественное тестирование)."""

    __tablename__ = "backtest_log"
    __table_args__ = (
        CheckConstraint(
            "event IN ('run', 'walk_forward', 'test_opened', 'test_rejected', "
            "'unlock', 'failed')",
            name="event",
        ),
        Index("ix_backtest_log_series", "root_id", "timeframe_code", "family"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event: Mapped[str] = mapped_column(String(16))
    root_id: Mapped[int | None] = mapped_column(
        ForeignKey("roots.id", ondelete="SET NULL")
    )
    timeframe_code: Mapped[str | None] = mapped_column(
        ForeignKey("timeframes.code", ondelete="RESTRICT")
    )
    family: Mapped[str | None] = mapped_column(String(64))
    experiment_id: Mapped[int | None] = mapped_column(
        ForeignKey("backtest_experiments.id", ondelete="SET NULL")
    )
    params_hash: Mapped[str | None] = mapped_column(String(64))
    period_from: Mapped[dt.date | None] = mapped_column(Date)
    period_to: Mapped[dt.date | None] = mapped_column(Date)
    touches_test: Mapped[bool] = mapped_column(Boolean, server_default="false")
    note: Mapped[str | None] = mapped_column(Text)


class BacktestTestLock(CreatedAtMixin, Base):
    """Test-период связки (root, TF, family): открыт один раз, затем заблокирован."""

    __tablename__ = "backtest_test_locks"
    __table_args__ = (
        UniqueConstraint("root_id", "timeframe_code", "family"),
        CheckConstraint("test_from <= test_to", name="test_order"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    root_id: Mapped[int] = mapped_column(ForeignKey("roots.id", ondelete="CASCADE"))
    timeframe_code: Mapped[str] = mapped_column(
        ForeignKey("timeframes.code", ondelete="RESTRICT")
    )
    family: Mapped[str] = mapped_column(String(64))
    test_from: Mapped[dt.date] = mapped_column(Date)
    test_to: Mapped[dt.date] = mapped_column(Date)
    experiment_id: Mapped[int | None] = mapped_column(
        ForeignKey("backtest_experiments.id", ondelete="SET NULL")
    )
