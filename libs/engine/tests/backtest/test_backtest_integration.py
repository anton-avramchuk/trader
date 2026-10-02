"""Бэктест на цепочке движков: инвариантность по префиксу, регрессия, WF."""

from datetime import datetime

import pytest

from tests.analogues.test_analogues_integration import BARS, items_at
from tests.events.dataset import perturb_future
from trader_engine.backtest.simulator import Costs, SimBar
from trader_engine.backtest.strategy import ExitRule, StrategySpec
from trader_engine.backtest.walk_forward import (
    BacktestData,
    Evaluation,
    WalkForwardConfig,
    apply_params,
    evaluate,
    select_params,
    trading_days,
    walk_forward,
)
from trader_engine.indicators import BarInput

TOUCH = StrategySpec(
    source="level_touch",
    stop=ExitRule("atr", 1.0),
    target=ExitRule("atr", 2.0),
    max_bars=8,
)
BREAK = StrategySpec(
    source="level_break",
    stop=ExitRule("atr", 1.0),
    target=ExitRule("atr", 2.0),
    max_bars=8,
)
COSTS = Costs(half_spread_ticks=1, slippage_ticks=1, commission_per_unit=5)


def data_for(bars: list[BarInput], as_of: datetime | None = None) -> BacktestData:
    items, _ = items_at(bars, as_of)
    series = items[0].series
    sim = [
        SimBar(b.timestamp, b.close_time, b.open, b.high, b.low, b.close)
        for b in series.bars
    ]
    return BacktestData(series, sim, items, 0.01, 10.0)


DATA = data_for(BARS)
DAYS = trading_days(DATA)


def run(data: BacktestData, spec: StrategySpec, **kw: object) -> Evaluation:
    return evaluate(data, spec, DAYS[0], DAYS[-1], costs=COSTS, **kw)  # type: ignore[arg-type]


def summary(ev: Evaluation) -> list[tuple[int, str, float]]:
    return [
        (p.trade.signal_index, p.trade.reason, round(p.trade.net_ticks, 6))
        for p in ev.priced
    ]


@pytest.mark.parametrize("spec", [TOUCH, BREAK])
@pytest.mark.parametrize("cut", [130, 150, 190])
def test_trades_closed_before_the_cut_do_not_depend_on_the_future(
    spec: StrategySpec, cut: int
) -> None:
    clean = run(DATA, spec)
    future = run(data_for(perturb_future(BARS, cut + 1)), spec)

    closed = BARS[cut].close_time
    before_clean = [p.trade for p in clean.priced if p.exit_time <= closed]
    before_future = [p.trade for p in future.priced if p.exit_time <= closed]

    assert before_clean, "в префиксе должны быть закрытые сделки"
    assert before_clean == before_future
    # и сигналы до среза те же: открытые сделки начинаются одинаково
    first_open = [
        p.trade.signal_index
        for p in clean.priced
        if p.trade.signal_index <= cut and p.exit_time > closed
    ]
    assert first_open == [
        p.trade.signal_index
        for p in future.priced
        if p.trade.signal_index <= cut and p.exit_time > closed
    ]


def test_period_evaluation_equals_full_run_restricted_to_the_period() -> None:
    end = DAYS[3]
    cut_index = max(i for i, b in enumerate(DATA.series.bars) if b.trading_day <= end)

    ev = evaluate(DATA, TOUCH, DAYS[0], end, costs=COSTS)
    full = run(DATA, TOUCH)

    closed = DATA.series.bars[cut_index].close_time
    inside = [
        p.trade
        for p in full.priced
        if p.exit_time <= closed and p.trade.reason != "end_of_data"
    ]
    ev_inside = [p.trade for p in ev.priced if p.trade.reason != "end_of_data"]
    assert inside == ev_inside
    assert all(p.exit_time <= closed for p in ev.priced)


def test_decisions_use_only_data_up_to_the_signal_bar() -> None:
    ev = run(DATA, BREAK)

    for p in ev.priced:
        trade = p.trade
        signal_bar = DATA.series.bars[trade.signal_index]
        entry_bar = DATA.series.bars[trade.entry_bar]
        assert trade.entry_bar == trade.signal_index + 1
        assert trade.entry_price == pytest.approx(entry_bar.open)
        assert trade.entry_time >= signal_bar.close_time  # вход после сигнала


def test_accounting_identities() -> None:
    ev = run(DATA, BREAK, quantity=3)

    assert ev.priced
    for p in ev.priced:
        t = p.trade
        assert t.gross_ticks == pytest.approx(
            (t.exit_price - t.entry_price) / 0.01 * (1 if t.side == "long" else -1)
        )
        assert t.net_ticks == pytest.approx(t.gross_ticks - 2 * 2.0)
        assert t.commission == pytest.approx(2 * 3 * 5)
        assert p.net_points == pytest.approx(t.net_ticks * 0.01 * 3)
        assert p.net_money == pytest.approx(t.net_ticks * 10.0 * 3 - t.commission)
        assert t.mfe_ticks >= min(t.gross_ticks, 0.0) - 1e-9
        assert t.mae_ticks <= max(t.gross_ticks, 0.0) + 1e-9


def test_regression_on_the_fixed_series() -> None:
    touch, brk = run(DATA, TOUCH), run(DATA, BREAK)

    assert len(touch.priced) == len(summary(touch)) == 6
    assert summary(touch)[:3] == REGRESSION_TOUCH
    assert len(brk.priced) == 30
    assert summary(brk)[:3] == REGRESSION_BREAK
    assert sum(p.trade.net_ticks for p in brk.priced) == pytest.approx(
        REGRESSION_BREAK_NET
    )
    assert brk.built.signals


REGRESSION_TOUCH: list[tuple[int, str, float]] = [
    (120, "target", 692.545234),
    (186, "stop", -312.598387),
    (192, "target", 581.643422),
]
REGRESSION_BREAK: list[tuple[int, str, float]] = [
    (41, "time", 9.0),
    (57, "target", 682.016081),
    (62, "target", 713.174185),
]
REGRESSION_BREAK_NET = 13456.491265857363


CONFIG = WalkForwardConfig(
    train_days=3,
    valid_days=1,
    step_days=1,
    grid={"stop_atr": [0.5, 1.0, 2.0], "target_atr": [1.0, 2.0]},
    min_trades=3,
)


def test_walk_forward_on_real_chain_is_deterministic_and_leaves_test_out() -> None:
    test_days = DAYS[-2:]
    usable = DAYS[: -len(test_days)]

    first = walk_forward(DATA, BREAK, CONFIG, usable, costs=COSTS)
    again = walk_forward(DATA, BREAK, CONFIG, usable, costs=COSTS)

    assert first == again and first.windows
    for w in first.windows:
        assert w.window.valid[1] < test_days[0]
    assert first.final_params is not None


@pytest.mark.parametrize("train_end", [3, 4])
def test_selection_on_train_does_not_see_later_bars(train_end: int) -> None:
    train = (DAYS[0], DAYS[train_end])
    cut = max(i for i, b in enumerate(DATA.series.bars) if b.trading_day <= train[1])
    future = data_for(perturb_future(BARS, cut + 1))

    clean = select_params(DATA, BREAK, train, CONFIG, DAYS, costs=COSTS)
    changed = select_params(future, BREAK, train, CONFIG, DAYS, costs=COSTS)

    assert clean[0] is not None
    assert clean[0] == changed[0] and clean[1] == changed[1]


def test_test_period_is_a_pure_function_of_the_final_parameters() -> None:
    usable, test = DAYS[:-2], (DAYS[-2], DAYS[-1])
    wf = walk_forward(DATA, BREAK, CONFIG, usable, costs=COSTS)
    assert wf.final_params is not None

    spec = apply_params(BREAK, wf.final_params)
    first = evaluate(DATA, spec, test[0], test[1], costs=COSTS)
    second = evaluate(DATA, spec, test[0], test[1], costs=COSTS)

    assert summary(first) == summary(second)
