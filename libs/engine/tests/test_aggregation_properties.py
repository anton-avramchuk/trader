"""Property-тесты агрегации: согласованность таймфреймов и границ баров."""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.test_aggregation import session_minutes
from trader_engine.aggregation import TIMEFRAMES, Bar, aggregate
from trader_engine.calendar import TradingCalendar, moex_forts_calendar
from trader_engine.ingest import Candle1m

FULL = moex_forts_calendar()
WITHOUT_WEEKEND = FULL.excluding_weekend_sessions()
CALENDARS = [FULL, WITHOUT_WEEKEND]
FAR_FUTURE = datetime(2100, 1, 1, tzinfo=UTC)

# Окна вокруг смен режима, особых дней и просто разные эпохи.
START_DATES = [
    date(2020, 11, 26),
    date(2021, 2, 25),  # введение утренней сессии 01.03.2021
    date(2022, 5, 27),  # основная до 18:50
    date(2022, 7, 8),  # возвращение вечерней сессии
    date(2024, 6, 10),  # отмена утренней сессии
    date(2025, 9, 11),  # вечер пятницы + выходные сессии + понедельник (эпоха next_day)
    date(2025, 10, 30),  # рабочая суббота 01.11.2025
    date(2026, 3, 18),  # смена режима 23.03.2026
    date(2026, 7, 10),  # старт в 07:00
    date(2026, 9, 25),
]


@st.composite
def candle_streams(draw: st.DrawFn) -> tuple[TradingCalendar, list[Candle1m]]:
    calendar = draw(st.sampled_from(CALENDARS))
    start = draw(st.sampled_from(START_DATES))
    days = draw(st.integers(min_value=3, max_value=8))
    moments = _minutes(calendar, start, days)
    # Небольшой набор минут и компактные атрибуты: тысячи отдельных draw
    # Hypothesis считает «слишком большим базовым примером».
    indices = draw(
        st.lists(
            st.integers(min_value=0, max_value=len(moments) - 1),
            min_size=1,
            max_size=300,
            unique=True,
        )
    )
    attributes = draw(
        st.lists(
            st.tuples(
                st.integers(min_value=1, max_value=100),  # low
                st.integers(min_value=0, max_value=20),  # размах high - low
                st.integers(min_value=0, max_value=20),  # смещение open
                st.integers(min_value=0, max_value=20),  # смещение close
                st.integers(min_value=0, max_value=1000),  # volume
            ),
            min_size=len(indices),
            max_size=len(indices),
        )
    )
    candles: list[Candle1m] = []
    for index, (low, spread, open_shift, close_shift, volume) in sorted(
        zip(indices, attributes, strict=True), key=lambda pair: pair[0]
    ):
        candles.append(
            Candle1m(
                timestamp=moments[index],
                open=Decimal(low + open_shift % (spread + 1)),
                high=Decimal(low + spread),
                low=Decimal(low),
                close=Decimal(low + close_shift % (spread + 1)),
                volume=Decimal(volume),
            )
        )
    return calendar, candles


_MINUTES_CACHE: dict[tuple[int, date, int], list[datetime]] = {}


def _minutes(calendar: TradingCalendar, start: date, days: int) -> list[datetime]:
    key = (id(calendar), start, days)
    if key not in _MINUTES_CACHE:
        _MINUTES_CACHE[key] = session_minutes(calendar, start, days)
    return _MINUTES_CACHE[key]


def all_bars(
    candles: list[Candle1m], calendar: TradingCalendar
) -> dict[str, list[Bar]]:
    return {
        timeframe: aggregate(candles, calendar, timeframe, complete_until=FAR_FUTURE)[0]
        for timeframe in TIMEFRAMES
    }


common = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


@common
@given(stream=candle_streams())
def test_volume_is_conserved_in_every_timeframe(
    stream: tuple[TradingCalendar, list[Candle1m]],
) -> None:
    calendar, candles = stream
    total = sum((c.volume for c in candles), Decimal(0))

    for timeframe, bars in all_bars(candles, calendar).items():
        assert sum((b.volume for b in bars), Decimal(0)) == total, timeframe
        assert sum(b.candles for b in bars) == len(candles), timeframe


@common
@given(stream=candle_streams())
def test_every_bar_equals_the_aggregate_of_its_minute_candles(
    stream: tuple[TradingCalendar, list[Candle1m]],
) -> None:
    calendar, candles = stream

    for timeframe, bars in all_bars(candles, calendar).items():
        for bar in bars:
            members = [
                c for c in candles if bar.timestamp <= c.timestamp < bar.close_time
            ]
            assert bar.candles == len(members), (timeframe, bar.timestamp)
            assert bar.open == members[0].open
            assert bar.close == members[-1].close
            assert bar.high == max(c.high for c in members)
            assert bar.low == min(c.low for c in members)
            assert bar.volume == sum((c.volume for c in members), Decimal(0))


@common
@given(stream=candle_streams())
def test_no_bar_crosses_a_trading_day_and_bars_are_ordered(
    stream: tuple[TradingCalendar, list[Candle1m]],
) -> None:
    calendar, candles = stream

    for timeframe, bars in all_bars(candles, calendar).items():
        stamps = [b.timestamp for b in bars]
        assert stamps == sorted(set(stamps)), timeframe
        length = TIMEFRAMES[timeframe]
        for bar in bars:
            assert bar.timestamp < bar.close_time
            if length is not None:
                assert bar.close_time - bar.timestamp <= length
                members = [
                    c for c in candles if bar.timestamp <= c.timestamp < bar.close_time
                ]
                assert {calendar.trading_day_of(c.timestamp) for c in members} == {
                    bar.trading_day
                }


@common
@given(stream=candle_streams())
def test_trading_day_never_goes_back_in_time(
    stream: tuple[TradingCalendar, list[Candle1m]],
) -> None:
    """Иначе один торговый день распадается на несколько баров (дубликаты ключа)."""
    calendar, candles = stream
    days = [calendar.trading_day_of(c.timestamp) for c in candles]
    known = [day for day in days if day is not None]

    assert len(known) == len(days)  # все свечи стримов лежат в сессиях
    assert known == sorted(known)


@common
@given(stream=candle_streams())
def test_daily_bars_are_aggregates_of_15m_bars_of_the_same_trading_day(
    stream: tuple[TradingCalendar, list[Candle1m]],
) -> None:
    calendar, candles = stream
    bars = all_bars(candles, calendar)
    by_day: dict[date, list[Bar]] = defaultdict(list)
    for bar in bars["15m"]:
        by_day[bar.trading_day].append(bar)

    daily = {bar.trading_day: bar for bar in bars["1d"]}

    assert set(daily) == set(by_day)
    for day, parts in by_day.items():
        parts.sort(key=lambda b: b.timestamp)
        assert daily[day].open == parts[0].open
        assert daily[day].close == parts[-1].close
        assert daily[day].high == max(b.high for b in parts)
        assert daily[day].low == min(b.low for b in parts)
        assert daily[day].volume == sum((b.volume for b in parts), Decimal(0))


@common
@given(stream=candle_streams())
def test_weekly_bars_are_aggregates_of_daily_bars_of_the_same_week(
    stream: tuple[TradingCalendar, list[Candle1m]],
) -> None:
    calendar, candles = stream
    bars = all_bars(candles, calendar)
    by_week: dict[date, list[Bar]] = defaultdict(list)
    for bar in bars["1d"]:
        by_week[calendar.trading_week_start(bar.trading_day)].append(bar)

    weekly = bars["1w"]

    assert len(weekly) == len(by_week)
    for bar, (_, parts) in zip(weekly, sorted(by_week.items()), strict=True):
        parts.sort(key=lambda b: b.timestamp)
        assert bar.open == parts[0].open
        assert bar.close == parts[-1].close
        assert bar.high == max(b.high for b in parts)
        assert bar.low == min(b.low for b in parts)
        assert bar.volume == sum((b.volume for b in parts), Decimal(0))


@pytest.mark.parametrize("timeframe", ["15m", "1h", "4h"])
def test_intraday_grid_over_the_regime_change_never_mixes_trading_days(
    timeframe: str,
) -> None:
    """Каждая минута сессий вокруг 23.03.2026: ни один бар не смешивает торговые дни."""
    candles = [
        Candle1m(
            timestamp=m,
            open=Decimal(1),
            high=Decimal(1),
            low=Decimal(1),
            close=Decimal(1),
            volume=Decimal(1),
        )
        for m in session_minutes(FULL, date(2026, 3, 19), 7)
    ]

    bars, stats = aggregate(candles, FULL, timeframe, complete_until=FAR_FUTURE)

    assert stats.skipped_outside_session == 0
    assert sum(b.candles for b in bars) == len(candles)
    assert all(
        {
            FULL.trading_day_of(c.timestamp)
            for c in candles
            if b.timestamp <= c.timestamp < b.close_time
        }
        == {b.trading_day}
        for b in bars
    )
    length = TIMEFRAMES[timeframe]
    assert length is not None
    assert timedelta(0) < (bars[0].close_time - bars[0].timestamp) <= length
