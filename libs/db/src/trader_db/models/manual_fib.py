"""Ручные сетки Фибоначчи: две точки на графике (ADR-0012)."""

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from trader_db.models.base import Base, CreatedAtMixin

PRICE = Numeric(24, 8)


class ManualFibGrid(CreatedAtMixin, Base):
    """Сетка, построенная вручную по двум точкам; в статистике не участвует.

    Хранится отдельно от прогонов движков (автосетки — события ``fib_grid``).
    Ряд — инструмент и таймфрейм, как у прогонов.
    """

    __tablename__ = "manual_fib_grids"
    __table_args__ = (
        CheckConstraint("start_time <> end_time", name="distinct_times"),
        CheckConstraint("start_price <> end_price", name="distinct_prices"),
        Index("ix_manual_fib_grids_instrument", "instrument_id", "timeframe_code"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE")
    )
    timeframe_code: Mapped[str] = mapped_column(
        ForeignKey("timeframes.code", ondelete="RESTRICT")
    )
    start_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    start_price: Mapped[Decimal] = mapped_column(PRICE)
    end_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    end_price: Mapped[Decimal] = mapped_column(PRICE)
    label: Mapped[str | None] = mapped_column(String(128))
