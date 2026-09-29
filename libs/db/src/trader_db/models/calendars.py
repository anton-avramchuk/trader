import datetime as dt
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Identity,
    String,
    Time,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from trader_db.models.base import Base


class CalendarRule(Base):
    """Расписание сессий, действующее в интервале дат (обе границы включены).

    ``weekday_windows`` / ``weekend_windows`` — массивы окон вида
    ``{"name": "main", "start": "10:00", "end": "14:00", "next_day": false}``
    в локальном времени календаря. Проверяет их движок при загрузке.
    """

    __tablename__ = "trading_calendar_rules"
    __table_args__ = (
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="period_valid",
        ),
        # Правила одного календаря не пересекаются по датам.
        ExcludeConstraint(
            ("calendar_id", "="),
            (text("daterange(effective_from, effective_to, '[]')"), "&&"),
            using="gist",
            name="ex_trading_calendar_rules_no_overlap",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    calendar_id: Mapped[int] = mapped_column(
        ForeignKey("trading_calendars.id", ondelete="CASCADE")
    )
    effective_from: Mapped[dt.date] = mapped_column(Date)
    effective_to: Mapped[dt.date | None] = mapped_column(Date)
    bar_anchor: Mapped[dt.time] = mapped_column(Time)
    weekday_windows: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    weekend_windows: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )


class CalendarHoliday(Base):
    """Дата, в которую биржа закрыта (окна этой даты не торгуются)."""

    __tablename__ = "trading_calendar_holidays"

    calendar_id: Mapped[int] = mapped_column(
        ForeignKey("trading_calendars.id", ondelete="CASCADE"), primary_key=True
    )
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(128))


class CalendarSpecialDay(Base):
    """Выходной, который торгуется как обычный день (рабочая суббота-перенос).

    Окна такого дня — как у будних дней действующего правила, а не выходные.
    """

    __tablename__ = "trading_calendar_special_days"

    calendar_id: Mapped[int] = mapped_column(
        ForeignKey("trading_calendars.id", ondelete="CASCADE"), primary_key=True
    )
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(128))
