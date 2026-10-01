"""Прогон периода и walk-forward: окна, выбор по train, причинность."""

import random
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from trader_engine.backtest.simulator import SimBar
from trader_engine.backtest.strategy import ExitRule, StrategySpec
from trader_engine.backtest.walk_forward import (
    BacktestData,
    Candidate,
    WalkForwardConfig,
    Window,
    WindowResult,
    apply_params,
    choose_final,
    evaluate,
    make_windows,
    param_grid,
    score,
    select_params,
    summarize,
    trading_days,
    walk_forward,
)
from trader_engine.indicators import BarInput
from trader_engine.stats.occurrences import Occurrence
from trader_engine.stats.pipeline import SeriesOccurrence, build_series

DAYS = 60
PER_DAY = 8
FIRST = date(2026, 1, 5)


def make_bars(seed: int = 3, cut: int | None = None) -> list[BarInput]:
    rng = random.Random(seed)
    bars: list[BarInput] = []
    price = 100.0
    for day in range(DAYS):
        d = FIRST + timedelta(days=day)
        for i in range(PER_DAY):
            opened = datetime(d.year, d.month, d.day, 6 + i, tzinfo=UTC)
            drift = 0.15 if (day // 10) % 2 == 0 else -0.15
            close = price + drift + rng.gauss(0, 1.0)
            if cut is not None and len(bars) >= cut:
                close = rng.uniform(20, 300)  # «будущее» заведомо другое
            bars.append(
                BarInput(
                    opened,
                    opened + timedelta(hours=1),
                    price,
                    max(price, close) + abs(rng.gauss(0, 0.4)),
                    min(price, close) - abs(rng.gauss(0, 0.4)),
                    close,
                    1.0,
                    d,
                )
            )
            price = close
    return bars


def make_data(bars: list[BarInput] | None = None, every: int = 9) -> BacktestData:
    bars = bars or make_bars()
    series = build_series("S", bars)
    sim = [
        SimBar(b.timestamp, b.close_time, b.open, b.high, b.low, b.close)
        for b in series.bars
    ]
    items = [
        SeriesOccurrence(
            series,
            Occurrence(
                key=f"e:{i}",
                engine="e",
                kind="pattern",
                group="double_bottom",
                direction="bullish" if (i // every) % 2 == 0 else "bearish",
                entry="confirmed",
                available_at=series.bars[i].close_time,
                quality=70.0,
            ),
        )
        for i in range(30, len(series.bars) - 1, every)
    ]
    return BacktestData(series, sim, items, 0.01, 10.0)


SPEC = StrategySpec(stop=ExitRule("atr", 1.5), target=ExitRule("atr", 3.0), max_bars=12)
CONFIG = WalkForwardConfig(
    train_days=15,
    valid_days=5,
    step_days=5,
    grid={"stop_atr": [0.5, 1.5, 3.0], "target_atr": [1.0, 3.0]},
    min_trades=5,
)


def test_trading_days_and_windows() -> None:
    days = trading_days(make_data())
    assert len(days) == DAYS and days == sorted(days)

    windows = make_windows(days[:10], 4, 2, 2)

    assert [(w.train, w.valid) for w in windows] == [
        ((days[0], days[3]), (days[4], days[5])),
        ((days[2], days[5]), (days[6], days[7])),
        ((days[4], days[7]), (days[8], days[9])),
    ]
    assert make_windows(days[:5], 4, 2, 1) == []


def test_evaluate_uses_only_signals_inside_the_period_and_cuts_bars_at_its_end() -> (
    None
):
    data = make_data()
    days = trading_days(data)
    start, end = days[20], days[29]

    ev = evaluate(data, SPEC, start, end)

    assert ev.priced
    bars = data.series.bars
    last_close = max(b.close_time for b in bars if b.trading_day <= end)
    for p in ev.priced:
        signal_day = bars[p.trade.signal_index].trading_day
        assert start <= signal_day <= end
        assert p.exit_time <= last_close  # никаких выходов после конца периода


def test_evaluation_does_not_see_bars_after_period_end() -> None:
    clean = make_data()
    days = trading_days(clean)
    end = days[29]
    cut = sum(1 for b in clean.series.bars if b.trading_day <= end)
    future = make_data(make_bars(cut=cut))

    a = evaluate(clean, SPEC, days[20], end)
    b = evaluate(future, SPEC, days[20], end)

    assert [p.trade for p in a.priced] == [p.trade for p in b.priced]


def test_param_grid_and_apply_params() -> None:
    combos = param_grid({"target_atr": [1, 2], "stop_atr": [0.5]})

    assert combos == [
        {"stop_atr": 0.5, "target_atr": 1},
        {"stop_atr": 0.5, "target_atr": 2},
    ]
    assert param_grid({}) == [{}]
    spec = apply_params(SPEC, {"stop_atr": 2.0, "max_bars": 5, "quality_min": 60})
    assert spec.stop == ExitRule("atr", 2.0) and spec.max_bars == 5
    assert spec.quality_min == 60 and spec.target == SPEC.target
    with pytest.raises(ValueError):
        param_grid({"bogus": [1]})
    with pytest.raises(ValueError):
        apply_params(SPEC, {"bogus": 1})


def test_score_rules() -> None:
    data = make_data()
    days = trading_days(data)
    metrics = summarize(evaluate(data, SPEC, days[10], days[40]), days, "ticks")

    assert metrics.trades > 5
    assert score(metrics, "net", 5) == metrics.net
    assert score(metrics, "profit_factor", 10_000) is None  # мало сделок
    if metrics.profit_factor is not None:
        assert score(metrics, "profit_factor", 5) == metrics.profit_factor


def test_selection_is_the_best_candidate_and_deterministic() -> None:
    data = make_data()
    days = trading_days(data)
    train = (days[0], days[24])

    first = select_params(data, SPEC, train, CONFIG, days)
    second = select_params(data, SPEC, train, CONFIG, days)

    assert first[0] == second[0]
    params, candidates, metrics = first
    assert len(candidates) == 6 and params is not None and metrics is not None
    best = max(c.score for c in candidates if c.score is not None)
    assert next(c for c in candidates if c.params == params).score == best


def test_selection_ignores_the_future_after_train() -> None:
    clean = make_data()
    days = trading_days(clean)
    train = (days[0], days[24])
    cut = sum(1 for b in clean.series.bars if b.trading_day <= train[1])
    future = make_data(make_bars(cut=cut))

    a = select_params(clean, SPEC, train, CONFIG, days)
    b = select_params(future, SPEC, train, CONFIG, days)

    assert a[0] == b[0] and a[1] == b[1]


def window(i: int, params: dict[str, Any] | None) -> WindowResult:
    d = FIRST + timedelta(days=i)
    return WindowResult(
        Window((d, d), (d, d)), params, [Candidate({}, 0, 0.0, None)], None, None, None
    )


def test_choose_final_prefers_frequency_then_recency() -> None:
    a, b = {"stop_atr": 1.0}, {"stop_atr": 2.0}

    assert choose_final([window(0, a), window(1, b), window(2, a)]) == a
    assert choose_final([window(0, a), window(1, b)]) == b  # равенство → поздний
    assert choose_final([window(0, None), window(1, None)]) is None
    assert choose_final([]) is None


def test_walk_forward_windows_and_validation_after_train() -> None:
    data = make_data()
    days = trading_days(data)

    result = walk_forward(data, SPEC, CONFIG, days[:45])

    assert len(result.windows) == (45 - 20) // 5 + 1
    for w in result.windows:
        assert w.window.train[1] < w.window.valid[0]
        if w.validation is not None:
            assert w.validation.start == w.window.valid[0]
            assert all(
                w.window.valid[0]
                <= data.series.bars[p.trade.signal_index].trading_day
                <= w.window.valid[1]
                for p in w.validation.priced
            )
    total = sum(w.valid_metrics.trades for w in result.windows if w.valid_metrics)
    assert result.validation_metrics.trades == total
    assert result.final_params is not None
    # test-период (последние дни) в окна не попал
    assert result.windows[-1].window.valid[1] <= days[44]


def test_walk_forward_is_deterministic_and_warns() -> None:
    data = make_data()
    days = trading_days(data)

    assert walk_forward(data, SPEC, CONFIG, days[:45]) == walk_forward(
        data, SPEC, CONFIG, days[:45]
    )
    assert walk_forward(data, SPEC, CONFIG, days[:10]).warnings == ["no_windows"]
    picky = WalkForwardConfig(5, 2, 5, CONFIG.grid, min_trades=10_000)
    assert walk_forward(data, SPEC, picky, days[:45]).warnings == [
        "no_qualifying_params"
    ]


@pytest.mark.parametrize(
    "config",
    [
        WalkForwardConfig(0, 5, 5),
        WalkForwardConfig(5, 0, 5),
        WalkForwardConfig(5, 5, 0),
        WalkForwardConfig(5, 5, 5, objective="sharpe"),  # type: ignore[arg-type]
        WalkForwardConfig(5, 5, 5, min_trades=0),
        WalkForwardConfig(5, 5, 5, grid={"bogus": [1]}),
    ],
)
def test_invalid_config_is_rejected(config: WalkForwardConfig) -> None:
    data = make_data()
    with pytest.raises(ValueError):
        walk_forward(data, SPEC, config, trading_days(data))
