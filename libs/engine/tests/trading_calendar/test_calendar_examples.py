"""Примеры с вручную посчитанными значениями (МСК = UTC+3, без летнего времени).

Расписание откалибровано по данным MOEX ISS (задача #59, ADR-0014).
"""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from trader_engine.calendar import (
    MOEX_FORTS_HOLIDAYS,
    SessionRule,
    SessionWindow,
    TradingCalendar,
    moex_forts_calendar,
)

M15 = timedelta(minutes=15)
H1 = timedelta(hours=1)
H4 = timedelta(hours=4)
MSK = timedelta(hours=3)


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def msk(day: date, clock: str) -> datetime:
    """Московское время ``ЧЧ:ММ`` указанного дня в UTC."""
    hour, minute = map(int, clock.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC) - MSK


@pytest.fixture
def calendar() -> TradingCalendar:
    return moex_forts_calendar()


def windows(
    calendar: TradingCalendar, day: date
) -> list[tuple[str, datetime, datetime]]:
    return [(s.name, s.start, s.end) for s in calendar.sessions_on(day)]


def expected(
    day: date, *items: tuple[str, str, str]
) -> list[tuple[str, datetime, datetime]]:
    return [(name, msk(day, start), msk(day, end)) for name, start, end in items]


# Эпохи расписания: (день, окна МСК). Проверены по минутным свечам ISS.
ERAS = [
    pytest.param(
        date(2020, 11, 30),
        [
            ("main", "10:00", "14:00"),
            ("main_2", "14:05", "18:45"),
            ("evening", "19:00", "23:50"),
        ],
        id="2020-без-утра",
    ),
    pytest.param(
        date(2021, 11, 30),
        [
            ("morning", "07:00", "10:00"),
            ("main", "10:00", "14:00"),
            ("main_2", "14:05", "18:45"),
            ("evening", "19:00", "23:50"),
        ],
        id="2021-утро-07:00",
    ),
    pytest.param(
        date(2022, 2, 25),
        [
            ("main", "10:00", "14:00"),
            ("main_2", "14:05", "18:45"),
            ("evening", "19:00", "23:50"),
        ],
        id="2022-02-утро-остановлено",
    ),
    pytest.param(
        date(2022, 4, 12),
        [("main", "10:00", "14:00"), ("main_2", "14:05", "18:45")],
        id="2022-04-только-основная",
    ),
    pytest.param(
        date(2022, 6, 15),
        [("main", "10:00", "14:00"), ("main_2", "14:05", "18:50")],
        id="2022-06-основная-до-18:50",
    ),
    pytest.param(
        date(2022, 8, 15),
        [
            ("main", "10:00", "14:00"),
            ("main_2", "14:05", "18:50"),
            ("evening", "19:05", "23:50"),
        ],
        id="2022-08-вечер-вернулся-19:05",
    ),
    pytest.param(
        date(2023, 3, 15),
        [
            ("morning", "08:59", "10:00"),
            ("main", "10:00", "14:00"),
            ("main_2", "14:05", "18:50"),
            ("evening", "19:05", "23:50"),
        ],
        id="2023-утро-09:00",
    ),
    pytest.param(
        date(2024, 11, 15),
        [
            ("main", "09:59", "14:00"),
            ("main_2", "14:05", "18:50"),
            ("evening", "19:05", "23:50"),
        ],
        id="2024-утро-отменено",
    ),
    pytest.param(
        date(2025, 3, 14),
        [
            ("morning", "08:59", "10:00"),
            ("main", "10:00", "14:00"),
            ("main_2", "14:05", "18:50"),
            ("evening", "19:05", "23:50"),
        ],
        id="2025-утро-вернулось",
    ),
    pytest.param(
        date(2026, 4, 15),
        [
            ("morning", "08:59", "10:00"),
            ("main", "10:00", "19:00"),
            ("evening", "19:00", "23:50"),
        ],
        id="2026-04-единая-сессия",
    ),
    pytest.param(
        date(2026, 9, 29),
        [
            ("morning", "06:59", "10:00"),
            ("main", "10:00", "19:00"),
            ("evening", "19:00", "23:50"),
        ],
        id="2026-09-старт-в-07:00",
    ),
]


class TestEras:
    @pytest.mark.parametrize(("day", "items"), ERAS)
    def test_windows_of_each_era(
        self, calendar: TradingCalendar, day: date, items: list[tuple[str, str, str]]
    ) -> None:
        assert windows(calendar, day) == expected(day, *items)

    def test_evening_of_the_legacy_regime_belongs_to_the_next_trading_day(
        self, calendar: TradingCalendar
    ) -> None:
        by_name = {
            s.name: s.trading_day for s in calendar.sessions_on(date(2026, 3, 18))
        }

        assert by_name == {
            "morning": date(2026, 3, 18),
            "main": date(2026, 3, 18),
            "main_2": date(2026, 3, 18),
            "evening": date(2026, 3, 19),
        }

    def test_unified_regime_evening_belongs_to_the_same_day(
        self, calendar: TradingCalendar
    ) -> None:
        days = {s.trading_day for s in calendar.sessions_on(date(2026, 9, 29))}

        assert days == {date(2026, 9, 29)}

    def test_no_sessions_before_history_start(self, calendar: TradingCalendar) -> None:
        assert calendar.sessions_on(date(2019, 12, 31)) == ()


class TestUnifiedRegime:
    def test_trading_day_bounds(self, calendar: TradingCalendar) -> None:
        assert calendar.trading_day_bounds(date(2026, 9, 29)) == (
            utc(2026, 9, 29, 3, 59),
            utc(2026, 9, 29, 20, 50),
        )

    def test_weekend_session_is_own_trading_day(
        self, calendar: TradingCalendar
    ) -> None:
        (session,) = calendar.sessions_on(date(2026, 9, 26))

        assert (session.start, session.end) == (
            utc(2026, 9, 26, 6, 59),
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
            utc(2026, 9, 28, 3, 59),
            utc(2026, 10, 4, 16),
        )
        assert calendar.excluding_weekend_sessions().trading_week_bounds(week) == (
            utc(2026, 9, 28, 3, 59),
            utc(2026, 10, 2, 20, 50),
        )

    def test_trading_week_start_is_monday(self, calendar: TradingCalendar) -> None:
        assert calendar.trading_week_start(date(2026, 9, 29)) == date(2026, 9, 28)
        assert calendar.trading_week_start(date(2026, 9, 27)) == date(2026, 9, 21)


class TestRegimeChange:
    def test_friday_evening_belongs_to_monday_across_the_change(
        self, calendar: TradingCalendar
    ) -> None:
        friday_evening = utc(2026, 3, 20, 17)

        assert calendar.trading_day_of(friday_evening) == date(2026, 3, 23)
        assert calendar.trading_day_bounds(date(2026, 3, 23)) == (
            utc(2026, 3, 20, 16, 5),
            utc(2026, 3, 23, 20, 50),
        )

    def test_weekend_before_the_change_has_no_session(
        self, calendar: TradingCalendar
    ) -> None:
        # На выходных 21–22.03.2026 торгов не было (по данным ISS).
        assert calendar.sessions_on(date(2026, 3, 21)) == ()
        assert calendar.sessions_on(date(2026, 3, 22)) == ()

    def test_clearing_disappears_on_the_change_day(
        self, calendar: TradingCalendar
    ) -> None:
        before = [s.name for s in calendar.sessions_on(date(2026, 3, 20))]
        after = [s.name for s in calendar.sessions_on(date(2026, 3, 23))]

        assert before == ["morning", "main", "main_2", "evening"]
        assert after == ["morning", "main", "evening"]


class TestWeekendsAndSpecialDays:
    def test_no_regular_weekend_sessions_before_august_2025(
        self, calendar: TradingCalendar
    ) -> None:
        assert calendar.sessions_on(date(2025, 3, 1)) == ()  # суббота

    def test_regular_weekend_session_since_august_2025(
        self, calendar: TradingCalendar
    ) -> None:
        saturday = date(2025, 9, 13)

        assert windows(calendar, saturday) == expected(
            saturday, ("weekend", "09:59", "19:00")
        )
        assert calendar.trading_day_of(msk(saturday, "12:00")) == saturday

    def test_some_weekends_have_no_session(self, calendar: TradingCalendar) -> None:
        assert calendar.sessions_on(date(2026, 9, 12)) == ()  # суббота без торгов

    def test_working_saturday_trades_like_a_weekday(
        self, calendar: TradingCalendar
    ) -> None:
        saturday = date(2025, 11, 1)  # рабочая суббота-перенос

        sessions = calendar.sessions_on(saturday)

        assert [s.name for s in sessions] == ["morning", "main", "main_2", "evening"]
        assert not any(s.is_weekend for s in sessions)
        assert sessions[-1].trading_day == date(2025, 11, 3)  # вечер — к следующему дню

    def test_working_saturday_survives_weekend_exclusion(
        self, calendar: TradingCalendar
    ) -> None:
        without = calendar.excluding_weekend_sessions()

        assert without.sessions_on(date(2025, 11, 1)) == calendar.sessions_on(
            date(2025, 11, 1)
        )

    def test_friday_evening_belongs_to_a_working_saturday(
        self, calendar: TradingCalendar
    ) -> None:
        assert calendar.trading_day_of(utc(2025, 10, 31, 17)) == date(2025, 11, 1)


class TestHolidays:
    def test_real_holidays_from_iss_data_have_no_sessions(
        self, calendar: TradingCalendar
    ) -> None:
        for day in (date(2020, 11, 4), date(2024, 5, 1), date(2025, 1, 7)):
            assert day in MOEX_FORTS_HOLIDAYS
            assert calendar.sessions_on(day) == ()

    def test_extra_holiday_has_no_sessions(self) -> None:
        calendar = moex_forts_calendar(extra_holidays=(date(2026, 9, 29),))

        assert calendar.sessions_on(date(2026, 9, 29)) == ()

    def test_evening_skips_holiday_and_weekend_to_next_trading_day(self) -> None:
        calendar = moex_forts_calendar(extra_holidays=(date(2026, 3, 20),))

        assert calendar.trading_day_of(utc(2026, 3, 19, 17)) == date(2026, 3, 23)


class TestExpectedGaps:
    def test_overnight_gap_is_expected(self, calendar: TradingCalendar) -> None:
        assert calendar.is_expected_gap(
            utc(2026, 9, 29, 20, 50), utc(2026, 9, 30, 3, 59)
        )

    def test_weekend_gap_is_expected_when_weekend_sessions_excluded(
        self, calendar: TradingCalendar
    ) -> None:
        without = calendar.excluding_weekend_sessions()

        assert without.is_expected_gap(
            utc(2026, 10, 2, 20, 50), utc(2026, 10, 5, 3, 59)
        )

    def test_gap_spanning_weekend_session_is_not_expected(
        self, calendar: TradingCalendar
    ) -> None:
        assert not calendar.is_expected_gap(
            utc(2026, 10, 2, 20, 50), utc(2026, 10, 5, 3, 59)
        )

    def test_clearing_gap_before_the_change(self, calendar: TradingCalendar) -> None:
        assert calendar.is_expected_gap(utc(2026, 3, 18, 11), utc(2026, 3, 18, 11, 5))

    def test_no_clearing_gap_after_the_change(self, calendar: TradingCalendar) -> None:
        assert not calendar.is_expected_gap(
            utc(2026, 9, 29, 11), utc(2026, 9, 29, 11, 5)
        )

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

    def test_auction_minute_makes_a_short_bar_before_the_first_full_one(
        self, calendar: TradingCalendar
    ) -> None:
        # Сделки аукциона открытия лежат в свече 06:59 МСК (03:59 UTC).
        slot = calendar.bar_slot(utc(2026, 9, 29, 3, 59), M15)

        assert slot is not None
        assert (slot.start, slot.close_time) == (
            utc(2026, 9, 29, 3, 45),
            utc(2026, 9, 29, 4),
        )
        assert slot.is_partial

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

    def test_clearing_makes_short_bar_before_the_change(
        self, calendar: TradingCalendar
    ) -> None:
        slot = calendar.bar_slot(utc(2026, 3, 18, 11, 7), M15)

        assert slot is not None
        assert (slot.start, slot.close_time) == (
            utc(2026, 3, 18, 11),
            utc(2026, 3, 18, 11, 15),
        )
        assert slot.is_partial

    def test_4h_bar_spanning_the_morning_start_is_one_partial_bar(
        self, calendar: TradingCalendar
    ) -> None:
        slot = calendar.bar_slot(utc(2026, 3, 18, 6), H4)  # 09:00 МСК

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

    def test_rules_cover_history_without_gaps(self) -> None:
        rules = moex_forts_calendar().rules
        for previous, current in zip(rules, rules[1:], strict=False):
            assert previous.effective_to is not None
            assert previous.effective_to + timedelta(days=1) == current.effective_from
