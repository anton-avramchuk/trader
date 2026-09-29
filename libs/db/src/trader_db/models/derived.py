"""Бары старших таймфреймов, собранные из минутных свечей (ADR-0018)."""

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from trader_db.models.base import PRICE, Base, CreatedAtMixin
from trader_db.models.raw import VOLUME


class DerivedCandle(Base):
    """Бар TF: кэш последнего построения по календарю и версии минутных данных.

    Бары детерминированно пересчитываются из raw 1m (чистая функция
    ``trader_engine.aggregation``); ``built_from_version_id`` — версия датасета,
    из которой построен именно этот бар (при инкрементальной пересборке версии
    у баров разные).
    """

    __tablename__ = "derived_candles"
    __table_args__ = (
        PrimaryKeyConstraint(
            "contract_id", "timeframe_code", "timestamp", name="pk_derived_candles"
        ),
        CheckConstraint("close_time > timestamp", name="close_after_open"),
        CheckConstraint("high >= low", name="high_ge_low"),
        CheckConstraint("high >= open AND high >= close", name="high_ge_open_close"),
        CheckConstraint("low <= open AND low <= close", name="low_le_open_close"),
        CheckConstraint("volume >= 0", name="volume_non_negative"),
        CheckConstraint("candles >= 1", name="candles_positive"),
        # Инкрементальная пересборка удаляет бары по торговому дню.
        Index(
            "ix_derived_candles_trading_day",
            "contract_id",
            "timeframe_code",
            "trading_day",
        ),
    )

    contract_id: Mapped[int] = mapped_column(
        ForeignKey("contracts.id", ondelete="RESTRICT")
    )
    timeframe_code: Mapped[str] = mapped_column(
        ForeignKey("timeframes.code", ondelete="RESTRICT")
    )
    # Open time бара (UTC) и явный ``close_time`` (ADR-0004).
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    close_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    open: Mapped[Decimal] = mapped_column(PRICE)
    high: Mapped[Decimal] = mapped_column(PRICE)
    low: Mapped[Decimal] = mapped_column(PRICE)
    close: Mapped[Decimal] = mapped_column(PRICE)
    volume: Mapped[Decimal] = mapped_column(VOLUME)
    trade_count: Mapped[int | None] = mapped_column(BigInteger)
    # Сколько минутных свечей вошло в бар.
    candles: Mapped[int] = mapped_column(Integer)
    is_partial: Mapped[bool] = mapped_column(Boolean)
    trading_day: Mapped[dt.date] = mapped_column(Date)
    built_from_version_id: Mapped[int] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="RESTRICT")
    )


class DerivedBuild(CreatedAtMixin, Base):
    """Журнал построений баров: чем и из чего построено, что пересобрано."""

    __tablename__ = "derived_builds"
    __table_args__ = (
        CheckConstraint("mode IN ('full', 'incremental')", name="mode_valid"),
        Index(
            "ix_derived_builds_contract_timeframe",
            "contract_id",
            "timeframe_code",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("contracts.id", ondelete="RESTRICT")
    )
    timeframe_code: Mapped[str] = mapped_column(
        ForeignKey("timeframes.code", ondelete="RESTRICT")
    )
    source_dataset_version_id: Mapped[int] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="RESTRICT")
    )
    # Отпечаток календаря, по которому построены бары; смена — полная пересборка.
    calendar_hash: Mapped[str] = mapped_column(String(64))
    include_weekend_sessions: Mapped[bool] = mapped_column(Boolean)
    algorithm_version: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(16))
    # Пересобрано с торгового дня (для полной — ``NULL``).
    rebuilt_from_day: Mapped[dt.date | None] = mapped_column(Date)
    complete_until: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    rows_deleted: Mapped[int] = mapped_column(BigInteger)
    rows_written: Mapped[int] = mapped_column(BigInteger)
    report: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
