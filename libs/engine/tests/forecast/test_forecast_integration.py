"""Forecast на цепочке движков: leakage, согласованность с MVP-5, калибровка."""

from datetime import datetime

import pytest

from tests.analogues.test_analogues_integration import (
    BARS,
    WINDOW,
    items_at,
    scaled_bars,
)
from tests.events.dataset import perturb_future
from trader_engine.analogues.library import build_candidates, formation_at
from trader_engine.analogues.search import find_analogues
from trader_engine.forecast.calibration import calibrate
from trader_engine.forecast.methods import (
    MethodForecast,
    empirical_forecast,
    knn_forecast,
)
from trader_engine.indicators import BarInput
from trader_engine.stats.pipeline import Filters, SeriesOccurrence, compute_statistics

HORIZONS = (3, 8)
GROUP = "level_break"


def group_items(
    items: list[SeriesOccurrence], direction: str
) -> list[SeriesOccurrence]:
    return [
        i
        for i in items
        if i.occurrence.group == GROUP and i.occurrence.direction == direction
    ]


def forecast_at(
    bars: list[BarInput], as_of: datetime | None, direction: str = "bearish"
) -> MethodForecast:
    items, last = items_at(bars, as_of)
    pool = group_items(items, direction)
    query = pool[-1]
    return empirical_forecast(
        query, items, as_of=last.close_time, horizons=HORIZONS, min_effective=5
    )


@pytest.mark.parametrize("cut", [150, 200, 250])
def test_empirical_at_as_of_does_not_depend_on_the_future(cut: int) -> None:
    as_of = BARS[cut].close_time
    clean = forecast_at(BARS, as_of)
    perturbed = forecast_at(perturb_future(BARS, cut + 1), as_of)

    assert clean.sample > 0
    assert clean == perturbed


@pytest.mark.parametrize("cut", [150, 250])
def test_knn_at_as_of_does_not_depend_on_the_future(cut: int) -> None:
    def knn(bars: list[BarInput]) -> MethodForecast:
        as_of = BARS[cut].close_time
        items, last = items_at(bars, as_of)
        series = items[0].series
        query = formation_at(series, len(series.bars) - 1, window=WINDOW, k=7)
        assert query is not None
        candidates = build_candidates(
            items, as_of=last.close_time, window=WINDOW, k=7, exclude=query
        )
        found = find_analogues(query, candidates, horizons=HORIZONS)
        return knn_forecast(found.matches, horizons=HORIZONS, min_effective=3)

    clean, perturbed = knn(BARS), knn(perturb_future(BARS, cut + 1))

    assert clean.sample > 0
    assert clean == perturbed


@pytest.mark.parametrize("direction", ["bullish", "bearish"])
def test_empirical_matches_mvp5_statistics(direction: str) -> None:
    items, last = items_at(BARS, None)
    pool = group_items(items, direction)
    query, rest = pool[-1], pool[:-1]

    result = empirical_forecast(
        query, items, as_of=last.close_time, horizons=HORIZONS, min_effective=1
    )
    stats = compute_statistics(
        rest,
        filters=Filters(groups=(GROUP,), direction=direction),
        horizons=HORIZONS,
        baseline=False,
    )

    [bucket] = stats.buckets
    for forecast, expected in zip(result.horizons, bucket.horizons, strict=True):
        assert expected.ret is not None
        assert forecast.n_effective == expected.n_effective
        assert forecast.n_raw == expected.n_raw
        sign = -1.0 if direction == "bearish" else 1.0  # ценовая система
        assert forecast.median_ret == pytest.approx(sign * expected.ret.median)
        assert forecast.mean_ret == pytest.approx(sign * expected.ret.mean)


def test_empirical_regime_filter_narrows_the_sample() -> None:
    items, last = items_at(BARS, None)
    query = group_items(items, "bearish")[-1]

    everything = empirical_forecast(query, items, as_of=last.close_time)
    same = empirical_forecast(query, items, as_of=last.close_time, same_regime=True)

    assert same.sample <= everything.sample
    assert everything.sample > 0


def test_forecasts_are_invariant_to_price_scale_in_atr() -> None:
    def run(bars: list[BarInput]) -> MethodForecast:
        items, last = items_at(bars, None)
        query = group_items(items, "bearish")[-1]
        return empirical_forecast(
            query, items, as_of=last.close_time, horizons=HORIZONS, min_effective=5
        )

    base, scaled = run(BARS), run(scaled_bars(3.0))

    for a, b in zip(base.horizons, scaled.horizons, strict=True):
        assert b.median_ret == pytest.approx(a.median_ret)
        assert [p.up for p in b.probabilities] == [p.up for p in a.probabilities]


def test_calibration_on_real_chain_is_causal_and_bounded() -> None:
    items, _ = items_at(BARS, None)
    pool = group_items(items, "bearish")
    cut = BARS[200].close_time

    report = calibrate(pool, horizon=3, min_history=5)
    early = calibrate(pool, horizon=3, min_history=5, until=cut)
    perturbed_items, _ = items_at(perturb_future(BARS, 204), None)
    perturbed = calibrate(
        group_items(perturbed_items, "bearish"), horizon=3, min_history=5, until=cut
    )

    assert 0 < report.tested <= len(pool)
    assert early.tested <= report.tested
    assert early == perturbed  # бары позже cut + горизонт не влияют
    for row in report.rows:
        assert 0 <= row.brier <= 1 and row.n == report.tested
        assert sum(b.n for b in row.bins) == row.n
