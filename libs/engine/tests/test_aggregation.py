from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from trader_engine.aggregation import TIMEFRAMES, BarAggregator, aggregate
from trader_engine.calendar import TradingCalendar, moex_forts_calendar
from trader_engine.ingest import Candle1m

CALENDAR = moex_forts_calendar()
FAR_FUTURE = datetime(2100, 1, 1, tzinfo=UTC)
MINUTE = timedelta(minutes=1)


def utc(
    month: int, day: int, hour: int, minute: int = 0, *, year: int = 2026
) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def candle(
    at: datetime,
    *,
    o: str = "10",
    h: str = "12",
    low: str = "9",
    c: str = "11",
    v: int = 1,
    trades: int | None = None,
) -> Candle1m:
    return Candle1m(
        timestamp=at,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal(v),
        trade_count=trades,
    )


class TestIntradayBars:
    def test_ohlcv_of_a_15m_bar(self) -> None:
        candles = [
            candle(utc(9, 29, 4, 0), o="10", h="12", low="9", c="11", v=5),
            candle(utc(9, 29, 4, 7), o="11", h="15", low="10", c="14", v=7),
            candle(utc(9, 29, 4, 14), o="14", h="14", low="8", c="9", v=3),
            candle(utc(9, 29, 4, 15)),
        ]

        bars, stats = aggregate(candles, CALENDAR, "15m")

        (bar,) = bars
        assert (bar.timestamp, bar.close_time) == (utc(9, 29, 4, 0), utc(9, 29, 4, 15))
        assert (bar.open, bar.high, bar.low, bar.close) == (
            Decimal("10"),
            Decimal("15"),
            Decimal("8"),
            Decimal("9"),
        )
        assert (bar.volume, bar.candles, bar.is_partial) == (Decimal(15), 3, False)
        assert bar.trading_day == date(2026, 9, 29)
        assert stats.last_bar_withheld is True  # бар с 04:15 ещё формируется

    def test_last_bar_is_published_only_when_known_to_be_closed(self) -> None:
        candles = [candle(utc(9, 29, 4, 0)), candle(utc(9, 29, 4, 15))]

        withheld, _ = aggregate(candles, CALENDAR, "15m")
        published, stats = aggregate(
            candles, CALENDAR, "15m", complete_until=utc(9, 29, 4, 30)
        )

        assert len(withheld) == 1
        assert len(published) == 2
        assert stats.last_bar_withheld is False

    def test_bar_closing_exactly_at_complete_until_is_published(self) -> None:
        bars, _ = aggregate(
            [candle(utc(9, 29, 4, 0))],
            CALENDAR,
            "15m",
            complete_until=utc(9, 29, 4, 15),
        )

        assert len(bars) == 1

    def test_end_of_session_bar_is_partial(self) -> None:
        candles = [candle(utc(9, 29, 20, 30)), candle(utc(9, 29, 20, 49))]

        (bar,) = aggregate(candles, CALENDAR, "1h", complete_until=FAR_FUTURE)[0]

        assert (bar.timestamp, bar.close_time) == (
            utc(9, 29, 20, 0),
            utc(9, 29, 20, 50),
        )
        assert bar.is_partial

    def test_auction_minute_forms_a_short_bar_before_the_first_full_one(self) -> None:
        candles = [candle(utc(9, 29, 3, 59)), candle(utc(9, 29, 4, 0))]

        bars, _ = aggregate(candles, CALENDAR, "15m", complete_until=FAR_FUTURE)

        first, second = bars
        assert (first.timestamp, first.close_time, first.is_partial, first.candles) == (
            utc(9, 29, 3, 45),
            utc(9, 29, 4, 0),
            True,
            1,
        )
        assert (second.timestamp, second.is_partial) == (utc(9, 29, 4, 0), False)

    def test_clearing_gap_makes_a_partial_bar(self) -> None:
        # До 23.03.2026 клиринг 14:00–14:05 МСК (11:00–11:05 UTC).
        candles = [
            candle(utc(3, 18, 10, 58)),
            candle(utc(3, 18, 10, 59)),
            candle(utc(3, 18, 11, 5)),
            candle(utc(3, 18, 11, 6)),
        ]

        before, after = aggregate(candles, CALENDAR, "15m", complete_until=FAR_FUTURE)[
            0
        ]

        assert (before.timestamp, before.candles, before.is_partial) == (
            utc(3, 18, 10, 45),
            2,
            False,
        )
        assert (after.timestamp, after.close_time, after.candles, after.is_partial) == (
            utc(3, 18, 11, 0),
            utc(3, 18, 11, 15),
            2,
            True,
        )

    def test_legacy_evening_bar_belongs_to_the_next_trading_day(self) -> None:
        bars, _ = aggregate(
            [candle(utc(3, 18, 16, 30))], CALENDAR, "1h", complete_until=FAR_FUTURE
        )

        assert bars[0].trading_day == date(2026, 3, 19)

    def test_bars_with_no_candles_are_not_invented(self) -> None:
        candles = [candle(utc(9, 29, 4, 0)), candle(utc(9, 29, 6, 0))]

        bars, _ = aggregate(candles, CALENDAR, "15m", complete_until=FAR_FUTURE)

        assert [b.timestamp for b in bars] == [utc(9, 29, 4, 0), utc(9, 29, 6, 0)]


class TestDailyAndWeeklyBars:
    def test_daily_bar_spans_the_trading_day(self) -> None:
        candles = [
            candle(utc(9, 29, 4, 0), o="10", h="12", low="9", c="11", v=1),
            candle(utc(9, 29, 10, 0), o="11", h="20", low="10", c="19", v=2),
            candle(utc(9, 29, 20, 49), o="19", h="19", low="5", c="6", v=3),
        ]

        (bar,) = aggregate(candles, CALENDAR, "1d", complete_until=FAR_FUTURE)[0]

        assert (bar.timestamp, bar.close_time) == (
            utc(9, 29, 3, 59),
            utc(9, 29, 20, 50),
        )
        assert (bar.open, bar.high, bar.low, bar.close, bar.volume, bar.candles) == (
            Decimal("10"),
            Decimal("20"),
            Decimal("5"),
            Decimal("6"),
            Decimal(6),
            3,
        )
        assert bar.trading_day == date(2026, 9, 29)
        assert not bar.is_partial

    def test_legacy_evening_joins_the_next_trading_day(self) -> None:
        candles = [
            candle(utc(3, 18, 16, 30), v=1),  # среда, 19:30 МСК — вечер четверга
            candle(utc(3, 19, 7, 30), v=2),  # четверг, 10:30 МСК
        ]

        (bar,) = aggregate(candles, CALENDAR, "1d", complete_until=FAR_FUTURE)[0]

        assert bar.trading_day == date(2026, 3, 19)
        assert bar.candles == 2
        assert bar.timestamp == utc(3, 18, 16, 5)  # начало вечерней сессии
        assert bar.volume == Decimal(3)

    def test_weekly_bar_spans_the_trading_week(self) -> None:
        candles = [
            candle(utc(9, 28, 4, 0), v=1),
            candle(utc(10, 2, 10, 0), v=2),
            candle(utc(10, 5, 4, 0), v=4),
        ]

        first, second = aggregate(candles, CALENDAR, "1w", complete_until=FAR_FUTURE)[0]

        assert (first.candles, first.volume) == (2, Decimal(3))
        # Неделя начинается с выходных сессий, которые относятся к её понедельнику.
        assert first.timestamp == utc(9, 26, 6, 59)
        assert first.trading_day == date(2026, 9, 28)  # день первой свечи недели
        assert (second.candles, second.timestamp) == (1, utc(10, 3, 6, 59))

    def test_weekly_bar_starts_with_the_weekend_sessions_that_belong_to_it(
        self,
    ) -> None:
        candles = [candle(utc(9, 28, 4, 0)), candle(utc(10, 5, 4, 0))]

        with_weekend = aggregate(candles, CALENDAR, "1w", complete_until=FAR_FUTURE)[0][
            0
        ]
        without = aggregate(
            candles,
            CALENDAR.excluding_weekend_sessions(),
            "1w",
            complete_until=FAR_FUTURE,
        )[0][0]

        assert with_weekend.timestamp == utc(9, 26, 6, 59)  # суббота
        assert without.timestamp == utc(9, 28, 3, 59)  # понедельник
        assert with_weekend.close_time == without.close_time == utc(10, 2, 20, 50)


class TestRegimeChange:
    """Смена режима 23.03.2026: вечерняя сессия пятницы принадлежит понедельнику."""

    CANDLES = [
        candle(utc(3, 20, 10, 0), v=1),  # пятница, основная
        candle(utc(3, 20, 17, 0), v=2),  # пятница, вечер 20:00 МСК → понедельник
        candle(utc(3, 23, 6, 0), v=4),  # понедельник, 09:00 МСК (утро с 08:59)
        candle(utc(3, 23, 10, 0), v=8),  # понедельник, день
    ]

    def test_daily_bars_split_at_the_trading_day_not_at_midnight(self) -> None:
        friday, monday = aggregate(
            self.CANDLES, CALENDAR, "1d", complete_until=FAR_FUTURE
        )[0]

        assert (friday.trading_day, friday.candles, friday.volume) == (
            date(2026, 3, 20),
            1,
            Decimal(1),
        )
        assert (monday.trading_day, monday.candles, monday.volume) == (
            date(2026, 3, 23),
            3,
            Decimal(14),
        )

    def test_weekly_bars_put_the_friday_evening_into_the_next_week(self) -> None:
        old_week, new_week = aggregate(
            self.CANDLES, CALENDAR, "1w", complete_until=FAR_FUTURE
        )[0]

        assert (old_week.candles, new_week.candles) == (1, 3)
        assert new_week.timestamp == utc(3, 20, 16, 5)  # с вечерней сессии пятницы

    def test_intraday_bars_do_not_mix_friday_and_monday(self) -> None:
        bars, _ = aggregate(self.CANDLES, CALENDAR, "4h", complete_until=FAR_FUTURE)

        assert [(b.trading_day, b.candles) for b in bars] == [
            (date(2026, 3, 20), 1),
            (date(2026, 3, 23), 1),
            (date(2026, 3, 23), 1),
            (date(2026, 3, 23), 1),
        ]


class TestInputHandling:
    def test_candles_outside_sessions_are_skipped_and_counted(self) -> None:
        candles = [candle(utc(9, 29, 20, 49)), candle(utc(9, 29, 20, 55))]

        bars, stats = aggregate(candles, CALENDAR, "15m", complete_until=FAR_FUTURE)

        assert len(bars) == 1
        assert stats.candles_in == 2
        assert stats.skipped_outside_session == 1
        assert stats.skipped_sample == [utc(9, 29, 20, 55)]

    def test_weekend_session_candles_are_dropped_when_weekend_is_excluded(self) -> None:
        saturday = candle(utc(9, 26, 8, 0))
        without = CALENDAR.excluding_weekend_sessions()

        kept, _ = aggregate([saturday], CALENDAR, "1d", complete_until=FAR_FUTURE)
        dropped, stats = aggregate([saturday], without, "1d", complete_until=FAR_FUTURE)

        assert len(kept) == 1
        assert dropped == []
        assert stats.skipped_outside_session == 1

    def test_working_saturday_is_kept_even_without_weekend_sessions(self) -> None:
        candles = [candle(datetime(2025, 11, 1, 8, 0, tzinfo=UTC))]
        without = CALENDAR.excluding_weekend_sessions()

        bars, _ = aggregate(candles, without, "1d", complete_until=FAR_FUTURE)

        assert [b.trading_day for b in bars] == [date(2025, 11, 1)]

    @pytest.mark.parametrize("second", [utc(9, 29, 4, 0), utc(9, 29, 3, 0)])
    def test_unsorted_or_repeated_candles_are_rejected(self, second: datetime) -> None:
        aggregator = BarAggregator(CALENDAR, "15m")
        aggregator.push(candle(utc(9, 29, 4, 0)))

        with pytest.raises(ValueError, match="по возрастанию"):
            aggregator.push(candle(second))

    def test_unknown_timeframe(self) -> None:
        with pytest.raises(ValueError, match="Неизвестный таймфрейм"):
            BarAggregator(CALENDAR, "2h")

    def test_trade_count_is_summed_or_unknown(self) -> None:
        known = [candle(utc(9, 29, 4, 0), trades=3), candle(utc(9, 29, 4, 1), trades=4)]
        unknown = [candle(utc(9, 29, 4, 0), trades=3), candle(utc(9, 29, 4, 1))]

        (with_trades,) = aggregate(known, CALENDAR, "15m", complete_until=FAR_FUTURE)[0]
        (without,) = aggregate(unknown, CALENDAR, "15m", complete_until=FAR_FUTURE)[0]

        assert with_trades.trade_count == 7
        assert without.trade_count is None

    def test_empty_input(self) -> None:
        bars, stats = aggregate([], CALENDAR, "1d", complete_until=FAR_FUTURE)

        assert bars == []
        assert (stats.candles_in, stats.bars_out, stats.last_bar_withheld) == (
            0,
            0,
            False,
        )

    def test_all_declared_timeframes_are_supported(self) -> None:
        assert list(TIMEFRAMES) == ["15m", "1h", "4h", "1d", "1w"]


def session_minutes(
    calendar: TradingCalendar, start: date, days: int
) -> list[datetime]:
    """Все минуты сессий календаря за ``days`` суток от ``start`` (UTC)."""
    begin = datetime(start.year, start.month, start.day, tzinfo=UTC) - timedelta(
        hours=3
    )
    minutes: list[datetime] = []
    for session in calendar.sessions_between(begin, begin + timedelta(days=days)):
        moment = max(session.start, begin)
        while moment < min(session.end, begin + timedelta(days=days)):
            minutes.append(moment)
            moment += MINUTE
    return minutes
