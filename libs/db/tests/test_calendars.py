"""Интеграционные тесты хранения календаря (нужен TRADER_DATABASE_URL)."""

from datetime import date, time, timedelta
from typing import Any

import pytest
from alembic import command
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from trader_engine.calendar import moex_forts_calendar

from trader_db import load_trading_calendar, make_engine
from trader_db.migrate import alembic_config
from trader_db.models import CalendarHoliday, CalendarRule, TradingCalendar

WINDOWS: list[dict[str, Any]] = [
    {"name": "main", "start": "10:00", "end": "14:00", "next_day": False}
]


def calendar_id(session: Session, code: str = "moex_forts") -> int:
    return session.scalars(
        select(TradingCalendar.id).where(TradingCalendar.code == code)
    ).one()


def new_rule(
    calendar: int, start: date, end: date | None, windows: list[dict[str, Any]]
) -> CalendarRule:
    return CalendarRule(
        calendar_id=calendar,
        effective_from=start,
        effective_to=end,
        bar_anchor=time(10, 0),
        weekday_windows=windows,
    )


def test_seeded_moex_calendar_behaves_like_engine_reference(session: Session) -> None:
    loaded = load_trading_calendar(session, "moex_forts")
    reference = moex_forts_calendar()

    day = date(2019, 12, 30)
    while day <= date(2027, 12, 31):
        assert loaded.sessions_on(day) == reference.sessions_on(day), day
        day += timedelta(days=1)


def test_seeded_calendar_uses_moscow_timezone(session: Session) -> None:
    assert load_trading_calendar(session, "moex_forts").timezone == "Europe/Moscow"


def test_holidays_are_applied(session: Session) -> None:
    session.add(
        CalendarHoliday(
            calendar_id=calendar_id(session), date=date(2026, 9, 29), name="test"
        )
    )
    session.flush()

    loaded = load_trading_calendar(session, "moex_forts")

    assert loaded.sessions_on(date(2026, 9, 29)) == ()
    assert loaded.sessions_on(date(2026, 9, 30)) != ()


def test_unknown_calendar_raises(session: Session) -> None:
    with pytest.raises(LookupError):
        load_trading_calendar(session, "nope")


def test_rules_of_one_calendar_cannot_overlap(session: Session) -> None:
    session.add(
        new_rule(calendar_id(session), date(2026, 3, 1), date(2026, 4, 1), WINDOWS)
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_open_ended_rule_overlaps_later_rules(session: Session) -> None:
    session.add(new_rule(calendar_id(session), date(2030, 1, 1), None, WINDOWS))

    with pytest.raises(IntegrityError):
        session.flush()


def test_adjacent_rules_are_allowed(session: Session) -> None:
    calendar = TradingCalendar(code="other", name="other")
    session.add(calendar)
    session.flush()
    session.add_all(
        [
            new_rule(calendar.id, date(2020, 1, 1), date(2020, 12, 31), WINDOWS),
            new_rule(calendar.id, date(2021, 1, 1), None, WINDOWS),
        ]
    )

    session.flush()


def test_different_calendars_may_share_a_period(session: Session) -> None:
    calendar = TradingCalendar(code="other", name="other")
    session.add(calendar)
    session.flush()
    session.add(new_rule(calendar.id, date(2026, 3, 23), None, WINDOWS))

    session.flush()


def test_rule_period_must_be_valid(session: Session) -> None:
    calendar = TradingCalendar(code="other", name="other")
    session.add(calendar)
    session.flush()
    session.add(new_rule(calendar.id, date(2026, 2, 1), date(2026, 1, 1), WINDOWS))

    with pytest.raises(IntegrityError):
        session.flush()


def test_seed_is_not_duplicated_after_downgrade_and_upgrade(
    temp_database_url: str,
) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "0001")
    command.upgrade(config, "head")

    engine = make_engine(temp_database_url)
    with Session(engine) as session:
        rules = session.scalar(select(func.count()).select_from(CalendarRule))
        calendars = session.scalar(
            select(func.count())
            .select_from(TradingCalendar)
            .where(TradingCalendar.code == "moex_forts")
        )
    engine.dispose()

    assert (rules, calendars) == (2, 1)
