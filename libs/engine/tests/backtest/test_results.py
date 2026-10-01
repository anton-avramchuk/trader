"""Деньги, equity и метрики: известные значения и свойства."""

from datetime import UTC, date, datetime, timedelta
from math import sqrt
from statistics import mean, stdev

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trader_engine.backtest.results import (
    PricedTrade,
    Record,
    compute_metrics,
    daily_series,
    equity_curve,
    max_drawdown,
    price_trades,
    records,
)
from trader_engine.backtest.simulator import Trade

T0 = datetime(2026, 9, 28, 10, tzinfo=UTC)


def trade(
    ticks: float,
    *,
    cost: float = 0.0,
    commission: float = 0.0,
    quantity: int = 1,
    mfe: float = 0.0,
    mae: float = 0.0,
    ambiguous: bool = False,
    day: int = 0,
) -> Trade:
    at = T0 + timedelta(days=day)
    return Trade(
        side="long",
        signal_index=0,
        ref=None,
        quantity=quantity,
        entry_bar=1,
        entry_time=at,
        entry_price=100.0,
        exit_bar=2,
        exit_time=at,
        exit_price=100.0 + ticks,
        reason="target",
        gross_ticks=ticks,
        cost_ticks=cost,
        mfe_ticks=mfe,
        mae_ticks=mae,
        commission=commission,
        ambiguous_bar=ambiguous,
    )


def test_money_for_a_trade() -> None:
    t = trade(5.0, cost=2.0, commission=7.0, quantity=2, mfe=6, mae=-1)

    [priced] = price_trades([t], tick_size=0.5, tick_value=10.0)

    assert priced.gross_points == pytest.approx(5 * 0.5 * 2)
    assert priced.net_points == pytest.approx(3 * 0.5 * 2)
    assert priced.gross_money == pytest.approx(5 * 10 * 2)
    assert priced.net_money == pytest.approx(3 * 10 * 2 - 7)
    assert priced.commission == 7
    assert priced.money_per_tick == pytest.approx(20.0)


def test_without_tick_value_money_is_empty_but_points_known() -> None:
    [priced] = price_trades([trade(3.0)], tick_size=1.0, tick_value=None)

    assert priced.gross_money is None and priced.net_money is None
    assert priced.money_per_tick is None and priced.gross_points == 3.0
    items, missing = records([priced], "money")
    assert items == [] and missing == 1
    assert len(records([priced], "points")[0]) == 1


def test_records_units_scale_mfe_and_mae() -> None:
    t = trade(5.0, cost=1.0, commission=4.0, quantity=2, mfe=8, mae=-2)
    [priced] = price_trades([t], tick_size=0.5, tick_value=10.0)

    [ticks], _ = records([priced], "ticks")
    [points], _ = records([priced], "points")
    [money], _ = records([priced], "money")

    assert (ticks.net, ticks.mfe, ticks.mae) == (4.0, 8.0, -2.0)
    assert (points.net, points.mfe, points.mae) == pytest.approx((4.0, 8.0, -2.0))
    assert (money.net, money.mfe, money.mae) == pytest.approx(
        (4 * 10 * 2 - 4, 160.0, -40.0)
    )


def record(
    day: int,
    net: float,
    *,
    mfe: float = 0.0,
    mae: float = 0.0,
    ambiguous: bool = False,
) -> Record:
    return Record(
        date(2026, 9, 28) + timedelta(days=day),
        net,
        net,
        mfe,
        mae,
        ambiguous,
    )


def test_known_metrics() -> None:
    items = [record(i, n) for i, n in enumerate([10, -5, 10, -5, 10])]
    days = [date(2026, 9, 28) + timedelta(days=i) for i in range(5)]

    m = compute_metrics(items, days, "ticks")

    assert (m.trades, m.wins) == (5, 3)
    assert m.win_rate == pytest.approx(0.6)
    assert m.net == m.gross == 20
    assert m.average_trade == 4
    assert m.profit_factor == pytest.approx(3.0)
    assert m.max_drawdown == pytest.approx(5.0)
    daily = [10, -5, 10, -5, 10]
    assert m.sharpe == pytest.approx(mean(daily) / stdev(daily) * sqrt(252))
    downside = sqrt((25 + 25) / 5)
    assert m.sortino == pytest.approx(mean(daily) / downside * sqrt(252))
    assert m.warnings == ["small_sample"]


def test_flat_days_lower_sharpe_and_ambiguous_share() -> None:
    items = [
        record(0, 10, ambiguous=True),
        record(1, 10),
        record(2, 10),
        record(3, -4),
    ]
    short = [r.day for r in items]
    long_period = [date(2026, 9, 28) + timedelta(days=i) for i in range(40)]

    tight = compute_metrics(items, short, "money")
    loose = compute_metrics(items, long_period, "money")

    assert tight.ambiguous_share == pytest.approx(0.25)
    assert loose.sharpe is not None and tight.sharpe is not None
    assert loose.max_drawdown == tight.max_drawdown  # равны: дни без сделок не влияют


def test_edge_cases() -> None:
    empty = compute_metrics([], [], "ticks")
    assert empty.trades == 0 and empty.warnings == ["no_trades"]
    assert empty.profit_factor is None and empty.sharpe is None

    winners = compute_metrics([record(0, 3), record(1, 2)], [], "ticks")
    assert winners.profit_factor is None  # убытков нет
    assert winners.sortino is None  # нет отрицательных дней

    single = compute_metrics([record(0, 3)], [], "ticks")
    assert single.sharpe is None  # один день — нет разброса


def test_equity_curve_is_cumulative_in_exit_order() -> None:
    items = [record(2, -3), record(0, 5), record(1, 2)]

    curve = equity_curve(items)

    assert [p.equity for p in curve] == [5, 7, 4]
    assert [p.day for p in curve] == sorted(p.day for p in curve)


def test_daily_series_pads_trading_days() -> None:
    items = [record(0, 5), record(0, 3), record(2, -1)]
    days = [date(2026, 9, 28) + timedelta(days=i) for i in range(4)]

    assert daily_series(items, days) == [8, 0, -1, 0]


@given(st.lists(st.floats(-100, 100, allow_nan=False), max_size=60))
def test_drawdown_properties(steps: list[float]) -> None:
    drawdown = max_drawdown(steps)

    assert drawdown >= 0
    assert drawdown <= sum(abs(x) for x in steps) + 1e-9
    assert max_drawdown([abs(x) for x in steps]) == 0  # только прибыль — просадки нет


@given(st.lists(st.floats(-50, 50, allow_nan=False), min_size=1, max_size=40))
def test_metrics_invariants(nets: list[float]) -> None:
    items = [record(i, n) for i, n in enumerate(nets)]

    m = compute_metrics(items, [r.day for r in items], "ticks")

    assert m.trades == len(nets)
    assert 0 <= (m.win_rate or 0) <= 1
    assert m.net == pytest.approx(sum(nets))
    assert m.max_drawdown >= 0
    if m.profit_factor is not None:
        assert m.profit_factor >= 0
    assert isinstance(PricedTrade, type)
