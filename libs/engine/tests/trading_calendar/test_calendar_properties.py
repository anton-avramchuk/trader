"""Property-тесты инвариантов календаря на всём диапазоне истории (2020–2027)."""

from datetime import UTC, date, datetime, timedelta

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from trader_engine.calendar import TradingCalendar, moex_forts_calendar

CALENDAR = moex_forts_calendar()
WEEKDAYS_ONLY = CALENDAR.excluding_weekend_sessions()
CALENDARS = [CALENDAR, WEEKDAYS_ONLY]

# Сетка баров, которую поддерживает платформа.
DURATIONS = [timedelta(minutes=15), timedelta(hours=1), timedelta(hours=4)]

instants = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2027, 12, 31),
    timezones=st.just(UTC),
)
dates = st.dates(min_value=date(2020, 1, 1), max_value=date(2027, 12, 31))
calendars = st.sampled_from(CALENDARS)
durations = st.sampled_from(DURATIONS)


@given(calendar=calendars, moment=instants, duration=durations)
def test_slot_contains_moment_and_respects_duration(
    calendar: TradingCalendar, moment: datetime, duration: timedelta
) -> None:
    session = calendar.session_at(moment)
    assume(session is not None)
    assert session is not None

    slot = calendar.bar_slot(moment, duration)

    assert slot is not None
    assert slot.start <= moment < slot.close_time
    assert slot.close_time - slot.start <= duration
    assert slot.trading_day == session.trading_day
    if not slot.is_partial:
        assert slot.close_time - slot.start == duration


@given(calendar=calendars, moment=instants, duration=durations)
def test_every_instant_of_a_slot_maps_back_to_the_same_slot(
    calendar: TradingCalendar, moment: datetime, duration: timedelta
) -> None:
    slot = calendar.bar_slot(moment, duration)
    assume(slot is not None)
    assert slot is not None

    for piece in calendar.sessions_between(slot.start, slot.close_time):
        first_instant = max(piece.start, slot.start)
        again = calendar.bar_slot(first_instant, duration)

        assert again is not None
        assert again.start == slot.start
        assert again.close_time == slot.close_time


@given(calendar=calendars, moment=instants)
def test_trading_day_bounds_contain_the_moment(
    calendar: TradingCalendar, moment: datetime
) -> None:
    day = calendar.trading_day_of(moment)
    assume(day is not None)
    assert day is not None

    bounds = calendar.trading_day_bounds(day)

    assert bounds is not None
    assert bounds[0] <= moment < bounds[1]


@given(calendar=calendars, moment=instants)
def test_trading_week_bounds_contain_the_moment(
    calendar: TradingCalendar, moment: datetime
) -> None:
    day = calendar.trading_day_of(moment)
    assume(day is not None)
    assert day is not None

    bounds = calendar.trading_week_bounds(calendar.trading_week_start(day))

    assert bounds is not None
    assert bounds[0] <= moment < bounds[1]


@given(calendar=calendars, day=dates)
def test_sessions_of_a_day_are_ordered_and_disjoint(
    calendar: TradingCalendar, day: date
) -> None:
    sessions = calendar.sessions_on(day)

    for session in sessions:
        assert session.start < session.end
    for previous, current in zip(sessions, sessions[1:], strict=False):
        assert previous.end <= current.start


@given(day=dates)
def test_weekend_exclusion_removes_only_weekend_sessions(day: date) -> None:
    kept = WEEKDAYS_ONLY.sessions_on(day)
    full = CALENDAR.sessions_on(day)

    assert all(not session.is_weekend for session in kept)
    assert kept == tuple(session for session in full if not session.is_weekend)


@settings(max_examples=200)
@given(
    calendar=calendars,
    start=instants,
    length=st.integers(min_value=1, max_value=3 * 24 * 3600),
)
def test_expected_gap_means_no_session_overlaps(
    calendar: TradingCalendar, start: datetime, length: int
) -> None:
    end = start + timedelta(seconds=length)

    first_day = start.date() - timedelta(days=1)
    last_day = end.date() + timedelta(days=1)
    overlapping = [
        session
        for offset in range((last_day - first_day).days + 1)
        for session in calendar.sessions_on(first_day + timedelta(days=offset))
        if session.start < end and session.end > start
    ]

    assert calendar.is_expected_gap(start, end) == (not overlapping)


@given(calendar=calendars, moment=instants)
def test_session_end_is_after_moment(
    calendar: TradingCalendar, moment: datetime
) -> None:
    end = calendar.session_end(moment)
    assume(end is not None)
    assert end is not None

    assert end > moment
    assert calendar.session_at(end - timedelta(microseconds=1)) is not None


GRID_WINDOWS = [
    (datetime(2021, 1, 1, tzinfo=UTC), datetime(2021, 7, 1, tzinfo=UTC)),
    (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 7, 1, tzinfo=UTC)),
]


@pytest.mark.parametrize("duration", DURATIONS)
def test_supported_durations_never_mix_trading_days(duration: timedelta) -> None:
    """Перебор всей сетки: старый режим и смена режима 23.03.2026, без ValueError."""
    checked = 0
    for start, stop in GRID_WINDOWS:
        moment = start
        while moment < stop:
            CALENDAR.bar_slot(moment, duration)
            moment += duration
            checked += 1

    assert checked > 0
