"""Симулятор: вход, стоп, цель, гэп, неоднозначный бар, время, ролл, издержки."""

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trader_engine.backtest.simulator import (
    Costs,
    Signal,
    SimBar,
    simulate,
)

START = datetime(2026, 9, 28, 4, tzinfo=UTC)


def sbar(
    i: int,
    o: float,
    h: float | None = None,
    low: float | None = None,
    c: float | None = None,
    *,
    contract: int = 1,
    factor: float = 1.0,
) -> SimBar:
    start = START + timedelta(hours=i)
    close = o if c is None else c
    return SimBar(
        start,
        start + timedelta(hours=1),
        o,
        max(o, close) if h is None else h,
        min(o, close) if low is None else low,
        close,
        contract,
        factor,
    )


def flat(n: int, price: float = 100.0, **kw: int | float) -> list[SimBar]:
    return [sbar(i, price, **kw) for i in range(n)]  # type: ignore[arg-type]


def run_one(bars: list[SimBar], signal: Signal, **kw: object):  # type: ignore[no-untyped-def]
    result = simulate(bars, [signal], tick_size=1.0, **kw)  # type: ignore[arg-type]
    assert len(result.trades) == 1
    return result.trades[0]


def test_entry_at_next_bar_open_not_signal_close() -> None:
    bars = flat(6)
    bars[2] = sbar(2, 100, c=108)  # сигнальный бар закрылся на 108
    bars[3] = sbar(3, 105, c=106)  # вход по open следующего бара

    trade = run_one(bars, Signal(2, "long", max_bars=1))

    leg = trade.legs[0]
    assert leg.entry_bar == 3 and leg.entry_price == 105
    assert leg.exit_price == 106 and trade.reason == "time"
    assert trade.gross_ticks == pytest.approx(1.0)


def test_signal_price_is_carried_to_the_trade() -> None:
    bars = flat(5)

    trade = run_one(bars, Signal(0, "long", max_bars=2, ref_price=123.5))

    assert trade.signal_price == 123.5
    assert run_one(bars, Signal(0, "long", max_bars=2)).signal_price is None


def test_stop_is_hit_inside_the_entry_bar_too() -> None:
    bars = flat(5)
    bars[1] = sbar(1, 100, 101, 94, 99)  # бар входа: падает до 94

    trade = run_one(bars, Signal(0, "long", stop=96.0))

    assert trade.reason == "stop" and trade.legs[0].exit_bar == 1
    assert trade.gross_ticks == pytest.approx(-4.0)
    assert not trade.ambiguous_bar


def test_target_for_short_and_side_sign() -> None:
    bars = flat(6)
    bars[2] = sbar(2, 100, 101, 95, 96)

    trade = run_one(bars, Signal(0, "short", stop=105.0, target=97.0))

    assert trade.reason == "target" and trade.legs[0].exit_bar == 2
    assert trade.gross_ticks == pytest.approx(3.0)  # шорт: 100 → 97


def test_gap_through_stop_fills_at_open() -> None:
    bars = flat(6)
    bars[2] = sbar(2, 90, 92, 88, 91)  # гэп вниз через стоп 96

    trade = run_one(bars, Signal(0, "long", stop=96.0))

    assert trade.reason == "stop"
    assert trade.legs[0].exit_price == 90
    assert trade.gross_ticks == pytest.approx(-10.0)


def test_gap_through_target_fills_at_open() -> None:
    bars = flat(6)
    bars[2] = sbar(2, 110, 112, 109, 111)

    trade = run_one(bars, Signal(0, "long", stop=95.0, target=105.0))

    assert trade.reason == "target" and trade.legs[0].exit_price == 110


def test_stop_and_target_in_one_bar_is_pessimistic_and_flagged() -> None:
    bars = flat(6)
    bars[2] = sbar(2, 100, 106, 94, 100)  # достижимы и стоп 95, и цель 105

    trade = run_one(bars, Signal(0, "long", stop=95.0, target=105.0))

    assert trade.reason == "stop" and trade.ambiguous_bar
    assert trade.gross_ticks == pytest.approx(-5.0)


def test_time_exit_counts_bars_like_mvp5_horizon() -> None:
    bars = [sbar(i, 100 + i, c=100 + i) for i in range(10)]

    trade = run_one(bars, Signal(1, "long", max_bars=3))

    # вход по open бара 2 (=102), выход по close бара 4 (=104): горизонт 3 бара
    assert trade.legs[0].entry_bar == 2 and trade.legs[0].exit_bar == 4
    assert trade.legs[0].exit_price == 104 and trade.reason == "time"


def test_end_of_data_closes_at_last_close() -> None:
    bars = [sbar(i, 100 + i, c=100 + i) for i in range(5)]

    trade = run_one(bars, Signal(1, "long"))

    assert trade.reason == "end_of_data"
    assert trade.legs[0].exit_bar == 4 and trade.legs[0].exit_price == 104


def test_costs_in_ticks_and_commission_per_side() -> None:
    bars = flat(6)
    bars[2] = sbar(2, 100, 106, 99, 105)

    trade = run_one(
        bars,
        Signal(0, "long", stop=90.0, target=105.0),
        costs=Costs(
            half_spread_ticks=0.5, slippage_ticks=0.5, commission_per_contract=7
        ),
        contracts=3,
    )

    assert trade.gross_ticks == pytest.approx(5.0)
    assert trade.cost_ticks == pytest.approx(2.0)  # 2 стороны × (0.5 + 0.5)
    assert trade.net_ticks == pytest.approx(3.0)
    assert trade.commission == pytest.approx(2 * 3 * 7)
    assert trade.contracts == 3


def test_mfe_and_mae_follow_the_path() -> None:
    bars = flat(8)
    bars[1] = sbar(1, 100, 104, 98, 101)
    bars[2] = sbar(2, 101, 109, 100, 108)
    bars[3] = sbar(3, 108, 108, 92, 93)

    trade = run_one(bars, Signal(0, "long", max_bars=3))

    assert trade.mfe_ticks == pytest.approx(9.0)
    assert trade.mae_ticks == pytest.approx(-8.0)


def test_roll_splits_trade_into_legs_with_contract_prices() -> None:
    # старый контракт 100, новый — в два раза дороже (factor 0.5 до шкалы continuous
    # не нужен: проверяем перевод уровня по factor нового бара)
    bars = [
        sbar(0, 100),
        sbar(1, 100),
        sbar(2, 102, c=102, contract=1),
        sbar(3, 210, 214, 208, 212, contract=2, factor=1.0),
        sbar(4, 212, 213, 211, 212, contract=2),
    ]

    trade = run_one(bars, Signal(1, "long", max_bars=3))

    assert trade.rolled and [leg.contract_id for leg in trade.legs] == [1, 2]
    first, second = trade.legs
    assert first.reason == "roll" and first.exit_bar == 2 and first.exit_price == 102
    assert second.entry_bar == 3 and second.entry_price == 210
    assert trade.reason == "time"
    assert trade.cost_ticks == 0 and trade.gross_ticks == pytest.approx(
        (102 - 102) + (212 - 210)
    )


def test_levels_are_converted_by_bar_factor_after_roll() -> None:
    # стоп 40 в шкале continuous: у старого контракта (factor 0.5) это цена 80,
    # у нового (factor 1.0) — 40
    bars = [
        sbar(0, 100, factor=0.5),
        sbar(1, 100, factor=0.5),
        sbar(2, 100, factor=0.5),
        sbar(3, 50, 51, 39, 45, contract=2, factor=1.0),
    ]

    trade = run_one(bars, Signal(0, "long", stop=40.0))

    assert trade.rolled
    assert trade.legs[0].reason == "roll"
    assert trade.legs[-1].reason == "stop" and trade.legs[-1].exit_price == 40


def test_roll_commission_and_costs_for_both_legs() -> None:
    bars = [
        sbar(0, 100),
        sbar(1, 100),
        sbar(2, 100, contract=2),
        sbar(3, 100, contract=2),
    ]

    trade = run_one(
        bars,
        Signal(0, "long", max_bars=3),
        costs=Costs(slippage_ticks=1, commission_per_contract=5),
    )

    assert len(trade.legs) == 2
    assert trade.cost_ticks == pytest.approx(4.0)  # 4 стороны × 1 тик
    assert trade.commission == pytest.approx(4 * 5)


def test_one_position_signals_while_open_are_skipped() -> None:
    bars = [sbar(i, 100 + i, c=100 + i) for i in range(20)]
    signals = [
        Signal(1, "long", max_bars=5),  # вход бар 2, выход close бара 6
        Signal(3, "long", max_bars=2),  # позиция открыта → пропуск
        Signal(6, "long", max_bars=2),  # сигнал на баре выхода — разрешён
        Signal(19, "long"),  # нет следующего бара
        Signal(99, "long"),  # вне данных
    ]

    result = simulate(bars, signals, tick_size=1.0)

    assert [t.signal_index for t in result.trades] == [1, 6]
    assert result.skipped.busy == 1
    assert result.skipped.no_next_bar == 1 and result.skipped.invalid == 1


def test_argument_validation() -> None:
    with pytest.raises(ValueError):
        simulate(flat(3), [], tick_size=0)
    with pytest.raises(ValueError):
        simulate(flat(3), [], tick_size=1, contracts=0)


@given(
    st.lists(st.floats(90, 110), min_size=6, max_size=40),
    st.floats(0.5, 10),
    st.floats(0.5, 10),
)
def test_invariants_on_random_walks(
    closes: list[float], stop: float, target: float
) -> None:
    bars = [
        sbar(i, closes[i - 1] if i else closes[0], c=closes[i])
        for i in range(len(closes))
    ]
    entry_ref = closes[0]
    signals = [
        Signal(
            i,
            "long" if i % 2 == 0 else "short",
            entry_ref - stop,
            entry_ref + target,
            5,
        )
        for i in range(0, len(bars) - 1, 3)
    ]

    result = simulate(bars, signals, tick_size=0.5, costs=Costs(slippage_ticks=1))

    last_exit = -1
    for trade in result.trades:
        assert trade.entry_bar >= trade.signal_index + 1
        assert trade.signal_index >= last_exit  # позиции не перекрываются
        assert trade.exit_bar >= trade.entry_bar
        assert trade.mfe_ticks >= max(0.0, trade.gross_ticks) - 1e-9
        assert trade.mae_ticks <= min(0.0, trade.gross_ticks) + 1e-9
        assert trade.cost_ticks == pytest.approx(2.0 * len(trade.legs))
        last_exit = trade.exit_bar
