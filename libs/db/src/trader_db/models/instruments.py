"""Инструменты и свечи (ADR-0028): абстрактные данные без контрактной модели."""

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from trader_db.models.base import PRICE, Base, CreatedAtMixin

VOLUME = Numeric(24, 4)


class Timeframe(Base):
    """Таймфрейм свечей: ``15m``, ``1h``, ``4h``, ``1d``, ``1w``."""

    __tablename__ = "timeframes"
    __table_args__ = (
        CheckConstraint("duration_seconds > 0", name="duration_positive"),
    )

    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    duration_seconds: Mapped[int] = mapped_column(Integer)
    sort_order: Mapped[int] = mapped_column(Integer, unique=True)
    is_internal: Mapped[bool] = mapped_column(Boolean, server_default=false())


class Instrument(CreatedAtMixin, Base):
    """Инструмент (тикер) источника; для ядра — просто ряд свечей по таймфреймам.

    ``tick_value`` — стоимость тика в валюте счёта на единицу (необязательна):
    по ней бэктест считает деньги; без неё результат — в тиках и пунктах.
    """

    __tablename__ = "instruments"
    __table_args__ = (
        CheckConstraint("tick_size > 0", name="tick_size_positive"),
        CheckConstraint(
            "tick_value IS NULL OR tick_value > 0", name="tick_value_positive"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    currency: Mapped[str] = mapped_column(String(8))
    tick_size: Mapped[Decimal] = mapped_column(PRICE)
    timezone: Mapped[str] = mapped_column(String(64))
    tick_value: Mapped[Decimal | None] = mapped_column(PRICE)
    source: Mapped[str] = mapped_column(String(32))


class Candle(Base):
    """Закрытая свеча инструмента; ``open_time`` — начало свечи (UTC).

    ``trading_day`` — дата открытия в часовом поясе инструмента.
    """

    __tablename__ = "candles"
    __table_args__ = (
        PrimaryKeyConstraint(
            "instrument_id", "timeframe_code", "open_time", name="pk_candles"
        ),
        ForeignKeyConstraint(["instrument_id"], ["instruments.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["timeframe_code"], ["timeframes.code"], ondelete="RESTRICT"
        ),
        CheckConstraint("close_time > open_time", name="close_after_open"),
        CheckConstraint("high >= low", name="high_not_below_low"),
        Index("ix_candles_close", "instrument_id", "timeframe_code", "close_time"),
    )

    instrument_id: Mapped[int] = mapped_column(BigInteger)
    timeframe_code: Mapped[str] = mapped_column(String(8))
    open_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    close_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    open: Mapped[Decimal] = mapped_column(PRICE)
    high: Mapped[Decimal] = mapped_column(PRICE)
    low: Mapped[Decimal] = mapped_column(PRICE)
    close: Mapped[Decimal] = mapped_column(PRICE)
    volume: Mapped[Decimal] = mapped_column(VOLUME)
    trading_day: Mapped[dt.date] = mapped_column(Date)


class CandleLoad(CreatedAtMixin, Base):
    """Журнал загрузок свечей из importer: что, за какой период и сколько строк."""

    __tablename__ = "candle_loads"
    __table_args__ = (
        CheckConstraint("period_from < period_to", name="period_order"),
        Index("ix_candle_loads_instrument", "instrument_id", "timeframe_code"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE")
    )
    timeframe_code: Mapped[str] = mapped_column(
        ForeignKey("timeframes.code", ondelete="RESTRICT")
    )
    period_from: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    period_to: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    rows: Mapped[int] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(32))
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL")
    )
