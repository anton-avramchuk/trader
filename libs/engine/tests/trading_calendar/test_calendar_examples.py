"""Примеры с вручную посчитанными значениями (МСК = UTC+3, без летнего времени)."""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from trader_engine.calendar import (
    SessionRule,
    SessionWindow,
    TradingCalendar,
    moex_forts_calendar,
)

M15 = timedelta(minutes=15)
H1 = timedelta(hours=1)
H4 = timedelta(hours=4)


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


@pytest.fixture
def calendar() -> TradingCalendar:
    return moex_forts_calendar()


def spans(
    calendar: TradingCalendar, day: date
) -> list[tuple[str, datetime, datetime, date]]:
    return [(s.name, s.start, s.end, s.trading_day) for s in calendar.sessions_on(day)]


class TestUnifiedRegime:
    def test_weekday_sessions(self, calendar: TradingCalendar) -> None:
        assert spans(calendar, date(2026, 9, 29)) == [
            ("morning", utc(2026, 9, 29, 4), utc(2026, 9, 29, 6), date(2026, 9, 29)),
            ("main", utc(2026, 9, 29, 6), utc(2026, 9, 29, 16), date(2026, 9, 29)),
            (
                "evening",
                utc(2026, 9, 29, 16),
                utc(2026, 9, 29, 20, 50),
                date(2026, 9, 29),
            ),
        ]

    def test_trading_day_bounds(self, calendar: TradingCalendar) -> None:
        assert calendar.trading_day_bounds(date(2026, 9, 29)) == (
            utc(2026, 9, 29, 4),
            utc(2026, 9, 29, 20, 50),
        )

    def test_weekend_session_is_own_trading_day(
        self, calendar: TradingCalendar
    ) -> None:
        (session,) = calendar.sessions_on(date(2026, 9, 26))

        assert (session.start, session.end) == (
            utc(2026, 9, 26, 7),
            utc(2026, 9, 26, 16),
        )
        assert session.trading_day == date(2026, 9, 26)
        assert session.is_weekend

    def test_weekend_sessions_can_be_excluded(self, calendar: TradingCalendar) -> None:
        without = calendar.excluding_weekend_sessions()

        assert without.sessions_on(date(2026, 9, 26)) == ()
        assert without.sessions_on(date(2026, 9, 29)) == calendar.sessions_on(
            date(2026, 9, 29)
        )

    def test_trading_week_bounds_with_and_without_weekend(
        self, calendar: TradingCalendar
    ) -> None:
        week = date(2026, 9, 28)

        assert calendar.trading_week_bounds(week) == (
            utc(2026, 9, 28, 4),
            utc(2026, 10, 4, 16),
        )
        assert calendar.excluding_weekend_sessions().trading_week_bounds(week) == (
            utc(2026, 9, 28, 4),
            utc(2026, 10, 2, 20, 50),
        )

    def test_trading_week_start_is_monday(self, calendar: TradingCalendar) -> None:
        assert calendar.trading_week_start(date(2026, 9, 29)) == date(2026, 9, 28)
        assert calendar.trading_week_start(date(2026, 9, 27)) == date(2026, 9, 21)


class TestLegacyRegime:
    def test_weekday_sessions_and_evening_belongs_to_next_day(
        self, calendar: TradingCalendar
    ) -> None:
        assert spans(calendar, date(2026, 3, 18)) == [
            (
                "morning",
                utc(2026, 3, 18, 4),
                utc(2026, 3, 18, 6, 50),
                date(2026, 3, 18),
            ),
            ("main", utc(2026, 3, 18, 7), utc(2026, 3, 18, 11), date(2026, 3, 18)),
            (
                "main_2",
                utc(2026, 3, 18, 11, 5),
                utc(2026, 3, 18, 15, 45),
                date(2026, 3, 18),
            ),
            (
                "evening",
                utc(2026, 3, 18, 16, 5),
                utc(2026, 3, 18, 20, 50),
                date(2026, 3, 19),
            ),
        ]

    def test_friday_evening_belongs_to_monday_across_regime_change(
        self, calendar: TradingCalendar
    ) -> None:
        friday_evening = utc(2026, 3, 20, 17)

        assert calendar.trading_day_of(friday_evening) == date(2026, 3, 23)
        assert calendar.trading_day_bounds(date(2026, 3, 23)) == (
            utc(2026, 3, 20, 16, 5),
            utc(2026, 3, 23, 20, 50),
        )

    def test_no_weekend_sessions_before_regime_change(
        self, calendar: TradingCalendar
    ) -> None:
        assert calendar.sessions_on(date(2026, 3, 21)) == ()
        assert calendar.sessions_on(date(2026, 3, 22)) == ()

    def test_unified_windows_start_on_regime_change_day(
        self, calendar: TradingCalendar
    ) -> None:
        names = [s.name for s in calendar.sessions_on(date(2026, 3, 23))]

        assert names == ["morning", "main", "evening"]

    def test_no_sessions_before_history_start(self, calendar: TradingCalendar) -> None:
        assert calendar.sessions_on(date(2019, 12, 31)) == ()


class TestHolidays:
    def test_holiday_has_no_sessions(self) -> None:
        calendar = moex_forts_calendar(holidays=(date(2026, 9, 29),))

        assert calendar.sessions_on(date(2026, 9, 29)) == ()

    def test_evening_skips_holiday_and_weekend_to_next_trading_day(self) -> None:
        calendar = moex_forts_calendar(holidays=(date(2026, 3, 20),))

        thursday_evening = utc(2026, 3, 19, 17)

        assert calendar.trading_day_of(thursday_evening) == date(2026, 3, 23)


class TestExpectedGaps:
    def test_overnight_gap_is_expected(self, calendar: TradingCalendar) -> None:
        assert calendar.is_expected_gap(utc(2026, 9, 29, 20, 50), utc(2026, 9, 30, 4))

    def test_weekend_gap_is_expected_when_weekend_sessions_excluded(
        self, calendar: TradingCalendar
    ) -> None:
        without = calendar.excluding_weekend_sessions()

        assert without.is_expected_gap(utc(2026, 10, 2, 20, 50), utc(2026, 10, 5, 4))

    def test_gap_spanning_weekend_session_is_not_expected(
        self, calendar: TradingCalendar
    ) -> None:
        assert not calendar.is_expected_gap(
            utc(2026, 10, 2, 20, 50), utc(2026, 10, 5, 4)
        )

    def test_clearing_gap_in_legacy_regime(self, calendar: TradingCalendar) -> None:
        assert calendar.is_expected_gap(utc(2026, 3, 18, 11), utc(2026, 3, 18, 11, 5))

    def test_gap_overlapping_a_session_is_a_real_gap(
        self, calendar: TradingCalendar
    ) -> None:
        assert not calendar.is_expected_gap(
            utc(2026, 3, 18, 10, 59), utc(2026, 3, 18, 11, 6)
        )

    def test_gap_inside_continuous_session_is_a_real_gap(
        self, calendar: TradingCalendar
    ) -> None:
        assert not calendar.is_expected_gap(utc(2026, 9, 29, 8), utc(2026, 9, 29, 8, 5))


class TestBarSlots:
    def test_full_15m_bar(self, calendar: TradingCalendar) -> None:
        slot = calendar.bar_slot(utc(2026, 9, 29, 4, 7), M15)

        assert slot is not None
        assert (slot.start, slot.close_time) == (
            utc(2026, 9, 29, 4),
            utc(2026, 9, 29, 4, 15),
        )
        assert not slot.is_partial
        assert slot.trading_day == date(2026, 9, 29)

    def test_bar_is_cut_by_session_end(self, calendar: TradingCalendar) -> None:
        slot = calendar.bar_slot(utc(2026, 9, 29, 20, 30), H1)

        assert slot is not None
        assert (slot.start, slot.close_time) == (
            utc(2026, 9, 29, 20),
            utc(2026, 9, 29, 20, 50),
        )
        assert slot.is_partial

    def test_4h_grid_is_anchored_at_seven(self, calendar: TradingCalendar) -> None:
        slot = calendar.bar_slot(utc(2026, 9, 29, 16, 30), H4)

        assert slot is not None
        assert (slot.start, slot.close_time) == (
            utc(2026, 9, 29, 16),
            utc(2026, 9, 29, 20),
        )
        assert not slot.is_partial

    def test_last_4h_bar_of_day_is_partial(self, calendar: TradingCalendar) -> None:
        slot = calendar.bar_slot(utc(2026, 9, 29, 20, 30), H4)

        assert slot is not None
        assert (slot.start, slot.close_time) == (
            utc(2026, 9, 29, 20),
            utc(2026, 9, 29, 20, 50),
        )
        assert slot.is_partial

    def test_outside_session_has_no_slot(self, calendar: TradingCalendar) -> None:
        assert calendar.bar_slot(utc(2026, 9, 29, 20, 55), M15) is None

    def test_clearing_makes_short_bar_in_legacy_regime(
        self, calendar: TradingCalendar
    ) -> None:
        slot = calendar.bar_slot(utc(2026, 3, 18, 11, 7), M15)

        assert slot is not None
        assert (slot.start, slot.close_time) == (
            utc(2026, 3, 18, 11),
            utc(2026, 3, 18, 11, 15),
        )
        assert slot.is_partial

    def test_4h_bar_spanning_auction_gap_is_one_partial_bar(
        self, calendar: TradingCalendar
    ) -> None:
        slot = calendar.bar_slot(utc(2026, 3, 18, 5), H4)

        assert slot is not None
        assert (slot.start, slot.close_time) == (
            utc(2026, 3, 18, 4),
            utc(2026, 3, 18, 8),
        )
        assert slot.is_partial
        assert slot.trading_day == date(2026, 3, 18)

    def test_legacy_evening_bar_belongs_to_next_trading_day(
        self, calendar: TradingCalendar
    ) -> None:
        slot = calendar.bar_slot(utc(2026, 3, 18, 16, 30), H1)

        assert slot is not None
        assert (slot.start, slot.close_time) == (
            utc(2026, 3, 18, 16),
            utc(2026, 3, 18, 17),
        )
        assert slot.is_partial
        assert slot.trading_day == date(2026, 3, 19)

    def test_bar_cannot_mix_trading_days(self) -> None:
        rule = SessionRule(
            effective_from=date(2020, 1, 1),
            effective_to=None,
            weekday_windows=(
                SessionWindow("day", time(10, 0), time(11, 0)),
                SessionWindow("evening", time(11, 0), time(12, 0), next_day=True),
            ),
            weekend_windows=(),
            bar_anchor=time(10, 0),
        )
        calendar = TradingCalendar("Europe/Moscow", [rule])

        with pytest.raises(ValueError, match="разных торговых дней"):
            calendar.bar_slot(utc(2026, 9, 29, 7, 30), timedelta(hours=2))


class TestValidation:
    def test_naive_datetime_is_rejected(self, calendar: TradingCalendar) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            calendar.session_at(datetime(2026, 9, 29, 10))

    def test_overlapping_rules_are_rejected(self) -> None:
        def rule(start: date, end: date | None) -> SessionRule:
            return SessionRule(
                effective_from=start,
                effective_to=end,
                weekday_windows=(),
                weekend_windows=(),
                bar_anchor=time(7, 0),
            )

        with pytest.raises(ValueError, match="пересекаются"):
            TradingCalendar(
                "Europe/Moscow",
                [
                    rule(date(2020, 1, 1), date(2021, 1, 1)),
                    rule(date(2021, 1, 1), None),
                ],
            )

    def test_window_must_end_after_start(self) -> None:
        with pytest.raises(ValueError):
            SessionWindow("bad", time(10, 0), time(10, 0))

    def test_windows_must_not_overlap(self) -> None:
        with pytest.raises(ValueError, match="пересекаются"):
            SessionRule(
                effective_from=date(2020, 1, 1),
                effective_to=None,
                weekday_windows=(
                    SessionWindow("a", time(10, 0), time(12, 0)),
                    SessionWindow("b", time(11, 0), time(13, 0)),
                ),
                weekend_windows=(),
                bar_anchor=time(7, 0),
            )

    def test_non_positive_duration_is_rejected(self, calendar: TradingCalendar) -> None:
        with pytest.raises(ValueError, match="положительной"):
            calendar.bar_slot(utc(2026, 9, 29, 10), timedelta(0))
