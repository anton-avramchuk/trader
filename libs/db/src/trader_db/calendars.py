"""Загрузка торгового календаря из БД в календарь движка."""

from datetime import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from trader_engine.calendar import SessionRule, SessionWindow
from trader_engine.calendar import TradingCalendar as EngineCalendar

from trader_db.models import CalendarHoliday, CalendarRule, TradingCalendar


def _window(raw: dict[str, Any]) -> SessionWindow:
    return SessionWindow(
        name=str(raw["name"]),
        start=time.fromisoformat(str(raw["start"])),
        end=time.fromisoformat(str(raw["end"])),
        next_day=bool(raw.get("next_day", False)),
    )


def _rule(row: CalendarRule) -> SessionRule:
    return SessionRule(
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        weekday_windows=tuple(_window(raw) for raw in row.weekday_windows),
        weekend_windows=tuple(_window(raw) for raw in row.weekend_windows),
        bar_anchor=row.bar_anchor,
    )


def load_trading_calendar(session: Session, code: str) -> EngineCalendar:
    """Календарь ``code`` с правилами и праздниками; ``LookupError``, если нет."""
    calendar = session.scalars(
        select(TradingCalendar).where(TradingCalendar.code == code)
    ).one_or_none()
    if calendar is None:
        raise LookupError(f"Торговый календарь {code!r} не найден")
    rules = session.scalars(
        select(CalendarRule).where(CalendarRule.calendar_id == calendar.id)
    ).all()
    holidays = session.scalars(
        select(CalendarHoliday.date).where(CalendarHoliday.calendar_id == calendar.id)
    ).all()
    return EngineCalendar(calendar.timezone, [_rule(r) for r in rules], holidays)
