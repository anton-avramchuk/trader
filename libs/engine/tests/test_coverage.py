from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from trader_engine.calendar import moex_forts_calendar
from trader_engine.coverage import Coverage, analyze_coverage

CALENDAR = moex_forts_calendar()
MINUTE = timedelta(minutes=1)
# Вторник 2026-09-29, режим «с 23.03.2026»: сессии 04:00–20:50 UTC без разрывов.
DAY = datetime(2026, 9, 29, 4, 0, tzinfo=UTC)
DAY_MINUTES = 17 * 60 - 10  # 04:00–20:50


def minutes(start: datetime, count: int) -> list[datetime]:
    return [start + i * MINUTE for i in range(count)]


def utc(month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


def test_empty_input_has_no_findings() -> None:
    assert analyze_coverage([], CALENDAR) == Coverage()


def test_complete_session_has_no_missing_minutes() -> None:
    result = analyze_coverage(minutes(DAY, DAY_MINUTES), CALENDAR)

    assert (
        result.missing_intervals,
        result.missing_minutes,
        result.outside_session,
    ) == (
        [],
        0,
        [],
    )


def test_hole_inside_session_is_reported() -> None:
    stamps = [
        m
        for m in minutes(DAY, DAY_MINUTES)
        if not utc(9, 29, 10) <= m < utc(9, 29, 10, 10)
    ]

    result = analyze_coverage(stamps, CALENDAR)

    assert result.missing_intervals == [(utc(9, 29, 10), utc(9, 29, 10, 10))]
    assert result.missing_minutes == 10


def test_hole_across_session_boundary_is_one_interval() -> None:
    # 06:00 UTC — стык утренней и основной сессий, разрыва между ними нет.
    stamps = [
        m
        for m in minutes(DAY, DAY_MINUTES)
        if not utc(9, 29, 5, 50) <= m < utc(9, 29, 6, 10)
    ]

    result = analyze_coverage(stamps, CALENDAR)

    assert result.missing_intervals == [(utc(9, 29, 5, 50), utc(9, 29, 6, 10))]
    assert result.missing_minutes == 20


def full_day(days: int = 0) -> list[datetime]:
    """Все минуты сессии дня: с аукционной 03:59 UTC до 20:49 UTC."""
    return minutes(DAY - MINUTE + timedelta(days=days), DAY_MINUTES + 1)


def test_overnight_gap_is_expected_not_missing() -> None:
    result = analyze_coverage(full_day() + full_day(1), CALENDAR)

    assert result.missing_minutes == 0
    assert result.outside_session == []


def test_whole_missing_day_is_reported() -> None:
    result = analyze_coverage(full_day() + full_day(2), CALENDAR)

    assert result.missing_intervals == [(utc(9, 30, 3, 59), utc(9, 30, 20, 50))]
    assert result.missing_minutes == DAY_MINUTES + 1


def test_weekend_is_expected_when_calendar_has_no_session_for_it() -> None:
    friday = minutes(utc(10, 2, 3, 59), DAY_MINUTES + 1)
    monday = minutes(utc(10, 5, 3, 59), DAY_MINUTES + 1)
    weekdays = CALENDAR.excluding_weekend_sessions()

    result = analyze_coverage(friday + monday, weekdays)

    assert result.missing_minutes == 0


def test_only_the_covered_range_is_checked() -> None:
    # Данные начинаются в 04:05: то, что было до первой свечи, источнику неизвестно.
    result = analyze_coverage(minutes(utc(9, 29, 4, 5), 30), CALENDAR)

    assert result.missing_minutes == 0


def test_candles_outside_sessions_are_listed() -> None:
    stamps = [utc(9, 29, 20, 49), utc(9, 29, 21, 0)]

    result = analyze_coverage(stamps, CALENDAR)

    assert result.outside_session == [utc(9, 29, 21, 0)]


def test_clearing_gap_of_legacy_regime_is_expected() -> None:
    # 11:00–11:05 UTC (14:00–14:05 МСК) — клиринг до 23.03.2026.
    stamps = [
        utc(3, 18, 10, 58),
        utc(3, 18, 10, 59),
        utc(3, 18, 11, 5),
        utc(3, 18, 11, 6),
    ]

    result = analyze_coverage(stamps, CALENDAR)

    assert result.missing_minutes == 0


@given(
    present=st.lists(
        st.integers(min_value=0, max_value=DAY_MINUTES - 1),
        min_size=1,
        max_size=200,
        unique=True,
    )
)
def test_missing_intervals_account_for_every_absent_minute(present: list[int]) -> None:
    stamps = sorted(DAY + i * MINUTE for i in present)

    result = analyze_coverage(stamps, CALENDAR)

    span = int((stamps[-1] + MINUTE - stamps[0]) / MINUTE)
    assert result.missing_minutes == span - len(stamps)
    assert sum(
        int((end - start) / MINUTE) for start, end in result.missing_intervals
    ) == (result.missing_minutes)
    assert result.outside_session == []
    previous_end = None
    for start, end in result.missing_intervals:
        assert start < end
        assert previous_end is None or previous_end < start  # интервалы склеены
        previous_end = end
        assert not any(start <= stamp < end for stamp in stamps)
