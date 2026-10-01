from datetime import UTC, date, datetime, timedelta

from trader_engine.indicators import BarInput
from trader_engine.stats.outcomes import (
    atr_series,
    compute_outcomes,
    entry_index,
)

START = datetime(2026, 9, 28, 4, tzinfo=UTC)  # понедельник


def bar(
    i: int,
    close: float,
    *,
    high: float | None = None,
    low: float | None = None,
    day: date | None = None,
    opened: datetime | None = None,
) -> BarInput:
    start = opened or START + timedelta(hours=i)
    return BarInput(
        timestamp=start,
        close_time=start + timedelta(hours=1),
        open=close,
        high=close if high is None else high,
        low=close if low is None else low,
        close=close,
        volume=1.0,
        trading_day=day or date(2026, 9, 28),
    )


def flat(n: int, price: float = 100.0) -> list[BarInput]:
    return [bar(i, price) for i in range(n)]


def test_bullish_return_mfe_mae_in_atr_and_percent() -> None:
    bars = flat(3)
    bars[1] = bar(1, 102, high=104, low=99)
    bars[2] = bar(2, 101, high=103, low=98)

    [out] = compute_outcomes(bars, 0, "bullish", 2.0, horizons=(2,))

    assert out.ret_pct == 1.0 and out.ret_atr == 0.5
    assert out.mfe_pct == 4.0 and out.mfe_atr == 2.0
    assert out.mae_pct == 2.0 and out.mae_atr == 1.0
    assert not out.censored


def test_bearish_mirrors_bullish() -> None:
    bars = flat(3)
    bars[1] = bar(1, 98, high=101, low=96)
    bars[2] = bar(2, 99, high=102, low=97)

    [out] = compute_outcomes(bars, 0, "bearish", 2.0, horizons=(2,))

    assert out.ret_pct == 1.0
    assert out.mfe_pct == 4.0
    assert out.mae_pct == 2.0


def test_excursions_are_never_negative() -> None:
    bars = flat(3)  # цена стоит: экскурсий нет

    [out] = compute_outcomes(bars, 0, "bullish", 1.0, horizons=(2,))

    assert out.mfe_pct == 0.0 and out.mae_pct == 0.0 and out.ret_pct == 0.0


def test_horizon_beyond_data_is_censored() -> None:
    [short, long] = compute_outcomes(flat(4), 0, "bullish", 1.0, horizons=(3, 4))

    assert not short.censored
    assert long.censored
    assert long.ret_atr is None and long.mfe_pct is None and long.first_hit is None


def test_missing_atr_keeps_percent_only() -> None:
    bars = flat(3)
    bars[2] = bar(2, 101, high=101)

    for atr in (None, 0.0):
        [out] = compute_outcomes(bars, 0, "bullish", atr, horizons=(2,))
        assert out.ret_atr is None and out.mfe_atr is None and out.mae_atr is None
        assert out.ret_pct == 1.0


def test_outcome_ignores_data_beyond_horizon() -> None:
    bars = flat(6)
    bars[1] = bar(1, 101, high=102)
    changed = list(bars)
    changed[4] = bar(4, 50, high=500, low=1)
    changed[5] = bar(5, 50, high=500, low=1)

    assert compute_outcomes(bars, 0, "bullish", 1.0, horizons=(3,)) == (
        compute_outcomes(changed, 0, "bullish", 1.0, horizons=(3,))
    )


def test_target_reached_first() -> None:
    bars = flat(4)
    bars[2] = bar(2, 105, high=106)

    [out] = compute_outcomes(bars, 0, "bullish", 1.0, target=105.5, horizons=(3,))

    assert out.first_hit == "target" and not out.ambiguous_bar


def test_bearish_target_uses_lows() -> None:
    bars = flat(3)
    bars[1] = bar(1, 99, low=94)

    [out] = compute_outcomes(bars, 0, "bearish", 1.0, target=95, horizons=(2,))

    assert out.first_hit == "target"


def test_invalidation_before_target_wins_and_later_target_is_ignored() -> None:
    bars = flat(5)
    bars[1] = bar(1, 99)
    bars[3] = bar(3, 110, high=111)
    invalidated_at = bars[1].close_time

    [out] = compute_outcomes(
        bars,
        0,
        "bullish",
        1.0,
        target=108,
        invalidated_at=invalidated_at,
        horizons=(4,),
    )

    assert out.first_hit == "invalidated" and not out.ambiguous_bar


def test_target_and_invalidation_in_same_bar_is_pessimistic() -> None:
    bars = flat(3)
    bars[1] = bar(1, 100, high=110, low=90)

    [out] = compute_outcomes(
        bars,
        0,
        "bullish",
        1.0,
        target=108,
        invalidated_at=bars[1].close_time,
        horizons=(2,),
    )

    assert out.first_hit == "invalidated"
    assert out.ambiguous_bar


def test_invalidation_outside_window_or_before_entry_is_not_a_hit() -> None:
    bars = flat(6)

    late = compute_outcomes(
        bars, 0, "bullish", 1.0, invalidated_at=bars[5].close_time, horizons=(3,)
    )
    early = compute_outcomes(
        bars, 2, "bullish", 1.0, invalidated_at=bars[1].close_time, horizons=(3,)
    )

    assert late[0].first_hit is None
    assert early[0].first_hit is None


def test_no_crossings_inside_a_continuous_session() -> None:
    [out] = compute_outcomes(flat(4), 0, "bullish", 1.0, horizons=(3,))

    assert not (out.crosses_session_gap or out.crosses_weekend)


def test_session_gap_is_a_pause_between_bars() -> None:
    bars = flat(4)
    bars[2] = bar(2, 100, opened=bars[1].close_time + timedelta(hours=9))
    bars[3] = bar(3, 100, opened=bars[2].close_time)

    [out] = compute_outcomes(bars, 0, "bullish", 1.0, horizons=(3,))

    assert out.crosses_session_gap
    assert not out.crosses_weekend


def test_weekend_crossing_detected_by_trading_day_week() -> None:
    friday, monday = date(2026, 10, 2), date(2026, 10, 5)
    bars = [
        bar(0, 100, day=friday),
        bar(1, 100, day=friday),
        bar(2, 100, day=monday),
        bar(3, 100, day=monday),
    ]

    [out] = compute_outcomes(bars, 0, "bullish", 1.0, horizons=(3,))
    [inside] = compute_outcomes(bars, 2, "bullish", 1.0, horizons=(1,))

    assert out.crosses_weekend
    assert not inside.crosses_weekend


def test_entry_index_matches_close_time_exactly() -> None:
    bars = flat(4)

    assert entry_index(bars, bars[2].close_time) == 2
    assert entry_index(bars, bars[2].close_time + timedelta(minutes=1)) is None
    assert entry_index(bars, START) is None


def test_atr_series_warms_up() -> None:
    bars = [bar(i, 100 + i, high=101 + i, low=99 + i) for i in range(6)]

    values = atr_series(bars, 3)

    assert values[:2] == [None, None]
    assert values[2] is not None and values[2] > 0
