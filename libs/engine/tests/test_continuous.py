"""Continuous-серия: расписание роллов, ratio, сегменты и масштаб цен."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trader_engine.aggregation import Bar
from trader_engine.calendar import moex_forts_calendar
from trader_engine.continuous import (
    ContractSpec,
    Roll,
    Segment,
    adjust_bar,
    build_segments,
    pick_ratio_basis,
    roll_schedule,
    roll_week_start,
    segment_at,
    to_contract_scale,
    to_current_scale,
)

CALENDAR = moex_forts_calendar()


def d(value: str) -> Decimal:
    return Decimal(value)


class TestRollWeek:
    def test_roll_is_the_latest_week_with_enough_trading_days(self) -> None:
        # Экспирация в четверг 29.10.2026: в неделе 26.10 только 3 торговых дня.
        assert roll_week_start(CALENDAR, date(2026, 10, 29), 5) == date(2026, 10, 19)
        assert roll_week_start(CALENDAR, date(2026, 10, 29), 3) == date(2026, 10, 26)

    def test_zero_days_means_the_expiration_week(self) -> None:
        assert roll_week_start(CALENDAR, date(2026, 10, 29), 0) == date(2026, 10, 26)

    def test_negative_days_are_rejected(self) -> None:
        with pytest.raises(ValueError):
            roll_week_start(CALENDAR, date(2026, 10, 29), -1)

    @given(
        expiration=st.dates(min_value=date(2021, 3, 1), max_value=date(2026, 12, 31)),
        days=st.integers(min_value=0, max_value=15),
    )
    def test_week_is_a_monday_and_leaves_at_least_n_days_but_not_a_week_more(
        self, expiration: date, days: int
    ) -> None:
        week = roll_week_start(CALENDAR, expiration, days)

        assert week.weekday() == 0
        assert week <= expiration
        left = sum(
            1
            for offset in range((expiration - week).days)
            if CALENDAR.is_trading_weekday(week + timedelta(days=offset))
        )
        assert left >= days
        later = week + timedelta(days=7)
        if later <= expiration:
            fewer = sum(
                1
                for offset in range((expiration - later).days)
                if CALENDAR.is_trading_weekday(later + timedelta(days=offset))
            )
            assert fewer < days


class TestSchedule:
    contracts = [
        ContractSpec(3, date(2027, 1, 28)),
        ContractSpec(1, date(2026, 10, 29)),
        ContractSpec(2, date(2026, 11, 26)),
    ]

    def test_rolls_go_between_neighbours_by_expiration(self) -> None:
        rolls = roll_schedule(self.contracts, CALENDAR, 5)

        assert [(r.from_contract_id, r.to_contract_id) for r in rolls] == [
            (1, 2),
            (2, 3),
        ]
        assert [r.week_start for r in rolls] == [date(2026, 10, 19), date(2026, 11, 16)]

    def test_roll_moment_starts_the_trading_week(self) -> None:
        for roll in roll_schedule(self.contracts, CALENDAR, 5):
            assert CALENDAR.trading_day_of(roll.at) == roll.week_start
            bounds = CALENDAR.trading_week_bounds(roll.week_start)
            assert bounds is not None and bounds[0] == roll.at

    def test_schedule_is_deterministic_and_input_order_independent(self) -> None:
        first = roll_schedule(self.contracts, CALENDAR, 5)
        second = roll_schedule(list(reversed(self.contracts)), CALENDAR, 5)

        assert first == second

    def test_single_contract_has_no_rolls(self) -> None:
        assert roll_schedule(self.contracts[:1], CALENDAR, 5) == []

    def test_duplicate_expiration_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="экспирацией"):
            roll_schedule(
                [
                    ContractSpec(1, date(2026, 10, 29)),
                    ContractSpec(2, date(2026, 10, 29)),
                ],
                CALENDAR,
                5,
            )

    def test_rolls_that_collapse_into_one_week_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="не позже предыдущего"):
            roll_schedule(
                [
                    ContractSpec(1, date(2026, 10, 29)),
                    ContractSpec(2, date(2026, 10, 30)),
                    ContractSpec(3, date(2026, 11, 2)),
                ],
                CALENDAR,
                0,
            )


class TestRatioBasis:
    def test_uses_the_latest_common_day(self) -> None:
        basis = pick_ratio_basis(
            {date(2026, 10, 15): d("100"), date(2026, 10, 16): d("110")},
            {date(2026, 10, 14): d("90"), date(2026, 10, 15): d("105")},
        )

        assert basis is not None
        assert basis.trading_day == date(2026, 10, 15)
        assert basis.ratio == d("1.05")

    def test_no_common_day_or_bad_price_gives_none(self) -> None:
        assert pick_ratio_basis({date(2026, 10, 1): d("1")}, {}) is None
        assert (
            pick_ratio_basis({date(2026, 10, 1): d("0")}, {date(2026, 10, 1): d("1")})
            is None
        )


def roll(from_id: int, to_id: int, at: datetime) -> Roll:
    return Roll(from_id, to_id, at.date(), at)


T1 = datetime(2026, 10, 19, 4, tzinfo=UTC)
T2 = datetime(2026, 11, 16, 4, tzinfo=UTC)


class TestSegments:
    def test_factors_are_products_of_later_ratios(self) -> None:
        segments = build_segments(
            [1, 2, 3], [(roll(1, 2, T1), d("1.1")), (roll(2, 3, T2), d("0.5"))]
        )

        assert [(s.contract_id, s.start, s.end) for s in segments] == [
            (1, None, T1),
            (2, T1, T2),
            (3, T2, None),
        ]
        assert [s.factor for s in segments] == [d("0.55"), d("0.5"), d("1")]

    def test_pending_roll_keeps_the_series_on_the_old_contract(self) -> None:
        segments = build_segments([1, 2, 3], [(roll(1, 2, T1), d("1.1"))])

        assert [(s.contract_id, s.end) for s in segments] == [(1, T1), (2, None)]
        assert build_segments([1, 2], []) == [Segment(1, None, None, d("1"))]

    def test_broken_chain_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="цепочку"):
            build_segments([1, 2, 3], [(roll(2, 3, T1), d("1"))])

    def test_segment_at_uses_half_open_intervals(self) -> None:
        segments = build_segments([1, 2], [(roll(1, 2, T1), d("2"))])

        before = segment_at(segments, T1 - timedelta(seconds=1))
        at = segment_at(segments, T1)
        assert before is not None and before.contract_id == 1
        assert at is not None and at.contract_id == 2

    def test_price_scaling_round_trips(self) -> None:
        segment = Segment(1, None, T1, d("1.25"))

        assert to_current_scale(d("80"), segment) == d("100")
        assert to_contract_scale(d("100"), segment) == d("80")


def make_bar(open_: str, high: str, low: str, close: str) -> Bar:
    return Bar(
        timeframe="1d",
        timestamp=T1,
        close_time=T1 + timedelta(hours=10),
        open=d(open_),
        high=d(high),
        low=d(low),
        close=d(close),
        volume=d("7"),
        trade_count=3,
        candles=600,
        is_partial=False,
        trading_day=T1.date(),
    )


def test_adjust_bar_scales_prices_but_not_volume() -> None:
    adjusted = adjust_bar(make_bar("10", "12", "9", "11"), Segment(1, None, T1, d("2")))

    bar = adjusted.bar
    assert (bar.open, bar.high, bar.low, bar.close) == (
        d("20"),
        d("24"),
        d("18"),
        d("22"),
    )
    assert bar.volume == d("7") and adjusted.contract_id == 1


def test_returns_inside_a_segment_are_unchanged() -> None:
    segment = Segment(1, None, T1, d("1.0837"))
    first = adjust_bar(make_bar("100", "101", "99", "100.5"), segment).bar
    second = adjust_bar(make_bar("100.5", "103", "100", "102.3"), segment).bar

    assert abs(second.close / first.close - d("102.3") / d("100.5")) < d("1e-20")


def test_identity_factor_returns_the_same_bar() -> None:
    bar = make_bar("1", "2", "1", "2")

    assert adjust_bar(bar, Segment(1, None, None, d("1"))).bar is bar
