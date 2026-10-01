"""Walk-forward калибровка: Brier, reliability, причинность, синтетика с известной p."""

import random
from dataclasses import replace

import pytest

from tests.forecast.test_methods import FAR, item
from tests.stats.test_outcomes import bar
from trader_engine.forecast.calibration import (
    BINS,
    calibrate,
    wilson,
)
from trader_engine.indicators import BarInput
from trader_engine.stats.occurrences import close_index
from trader_engine.stats.pipeline import Series, SeriesOccurrence

HORIZON = 4
STEP = 10  # события реже горизонта: исходы независимы и не перекрываются


def random_walk_series(n: int, p_up: float, seed: int = 1) -> Series:
    """Ряд, где за каждые STEP баров цена идёт на +2 с вероятностью p_up, иначе −2."""
    rng = random.Random(seed)
    price = 100.0
    bars: list[BarInput] = []
    direction = 1.0
    for i in range(n):
        if i % STEP == 0:
            direction = 1.0 if rng.random() < p_up else -1.0
        # внутри окна цена движется линейно к цели, чтобы горизонт 4 видел ±1.6
        price += direction * 0.4
        bars.append(bar(i, price))
    return Series("S", bars, [1.0] * n, [None] * n, close_index(bars))


def events(series: Series) -> list[SeriesOccurrence]:
    return [item(series, e) for e in range(STEP, len(series.bars) - HORIZON - 1, STEP)]


def test_wilson_interval() -> None:
    low, high = wilson(50, 100)
    assert low < 0.5 < high and high - low < 0.21
    assert wilson(0, 10)[0] == 0.0 and wilson(10, 10)[1] == 1.0
    with pytest.raises(ValueError):
        wilson(0, 0)


def test_known_probability_is_recovered_and_climatology_is_not_beaten() -> None:
    series = random_walk_series(3000, p_up=0.3)

    report = calibrate(events(series), horizon=HORIZON, min_history=20)

    assert report.tested > 200 and report.skipped >= 1
    row = next(r for r in report.rows if r.threshold == 1.0 and r.side == "up")
    # доход за горизонт ±1.6 ATR: P(≥ +1) = 0.3; прогноз по истории её воспроизводит
    assert row.brier == pytest.approx(0.3 * 0.7, abs=0.03)
    assert row.skill is not None and abs(row.skill) < 0.1
    populated = [b for b in row.bins if b.n]
    assert populated and sum(b.n for b in row.bins) == row.n
    for b in populated:
        assert b.mean_predicted == pytest.approx(0.3, abs=0.12)
        assert b.observed_low is not None and b.observed_high is not None
        assert b.observed_low <= 0.3 <= b.observed_high  # частота около p


def test_both_sides_and_all_thresholds_reported() -> None:
    series = random_walk_series(1500, p_up=0.5, seed=4)

    report = calibrate(events(series), horizon=HORIZON, thresholds=(0.5, 1.0))

    assert {(r.threshold, r.side) for r in report.rows} == {
        (0.5, "up"),
        (0.5, "down"),
        (1.0, "up"),
        (1.0, "down"),
    }
    assert all(len(r.bins) == BINS for r in report.rows)
    assert all(0 <= r.brier <= 1 for r in report.rows)


def test_not_enough_history_warns() -> None:
    series = random_walk_series(200, p_up=0.5)

    report = calibrate(events(series), horizon=HORIZON, min_history=500)

    assert report.tested == 0 and report.rows == []
    assert report.warnings == ["not_enough_history"]


def test_small_sample_warns() -> None:
    series = random_walk_series(300, p_up=0.5)

    report = calibrate(events(series), horizon=HORIZON, min_history=10)

    assert 0 < report.tested < 30 and "small_sample" in report.warnings


def test_forecast_for_an_event_uses_only_closed_outcomes() -> None:
    series = random_walk_series(1200, p_up=0.5, seed=7)
    cut = 600
    until = series.bars[cut].close_time
    perturbed_bars = [
        replace(b, close=b.close + 50.0, high=b.high + 50.0, low=b.low + 50.0)
        if i > cut + HORIZON
        else b
        for i, b in enumerate(series.bars)
    ]
    perturbed = Series(
        "S", perturbed_bars, series.atrs, series.regimes, close_index(perturbed_bars)
    )

    clean = calibrate(events(series), horizon=HORIZON, until=until)
    changed = calibrate(events(perturbed), horizon=HORIZON, until=until)

    assert clean.tested > 0
    assert clean == changed  # бары позже cut + горизонт на отчёт до cut не влияют


def test_deterministic_and_until_limits_tested() -> None:
    series = random_walk_series(1500, p_up=0.4, seed=9)
    items = events(series)

    assert calibrate(items, horizon=HORIZON) == calibrate(items, horizon=HORIZON)
    early = calibrate(items, horizon=HORIZON, until=series.bars[500].close_time)
    assert early.tested < calibrate(items, horizon=HORIZON).tested
    assert series.bars[-1].close_time < FAR
