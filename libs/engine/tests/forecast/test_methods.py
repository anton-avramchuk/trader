"""Empirical-прогноз: выборка, направление, режим, причинность."""

from dataclasses import replace
from datetime import timedelta

import pytest

from tests.forecast.test_core import observations
from tests.stats.test_outcomes import START, bar
from trader_engine.analogues.library import build_candidates, formation_at
from trader_engine.analogues.search import AnalogueMatch, find_analogues
from trader_engine.forecast.core import forecast_horizon, to_price_frame
from trader_engine.forecast.methods import empirical_forecast, knn_forecast
from trader_engine.stats.occurrences import Occurrence, close_index
from trader_engine.stats.pipeline import Series, SeriesOccurrence
from trader_engine.stats.regime import Regime

N = 200
WINDOW = 30
FAR = START + timedelta(days=3650)
UP, DOWN = Regime("uptrend", "mid"), Regime("downtrend", "mid")


def rising_series(regimes: list[Regime | None] | None = None) -> Series:
    bars = [bar(i, 100.0 + 0.5 * i) for i in range(N)]  # +0.5 за бар, ATR = 1
    return Series("S", bars, [1.0] * N, regimes or [None] * N, close_index(bars))


def item(
    series: Series,
    entry: int,
    *,
    direction: str = "bullish",
    group: str = "double_bottom",
) -> SeriesOccurrence:
    return SeriesOccurrence(
        series,
        Occurrence(
            key=f"e:{group}:{direction}:{entry}",
            engine="e",
            kind="pattern",
            group=group,
            direction=direction,  # type: ignore[arg-type]
            entry="confirmed",
            available_at=series.bars[entry].close_time,
        ),
    )


def test_to_price_frame_mirrors_everything() -> None:
    event = forecast_horizon(
        observations([-2.0, -1.0, 0.5, 1.5, 3.0, 0.2]), horizon=5, min_effective=1
    )
    price = to_price_frame(event)

    assert price.mean_ret == pytest.approx(-event.mean_ret)  # type: ignore[operator]
    assert price.quantiles[10] == pytest.approx(-event.quantiles[90])
    assert price.quantiles[90] == pytest.approx(-event.quantiles[10])
    assert price.median_mfe == event.median_mae
    for a, b in zip(event.probabilities, price.probabilities, strict=True):
        assert (b.up, b.down) == (a.down, a.up)
    assert to_price_frame(to_price_frame(event)) == event


def test_bullish_history_in_rising_market_forecasts_up() -> None:
    series = rising_series()
    history = [item(series, e) for e in range(20, 150, 10)]
    query = item(series, 160)

    result = empirical_forecast(
        query, history, as_of=FAR, horizons=(4,), min_effective=1
    )

    [h] = result.horizons
    assert result.method == "empirical" and result.sample == len(history)
    assert h.median_ret == pytest.approx(2.0)  # 4 бара × 0.5 / ATR 1
    assert all(p.up in (0.0, 1.0) for p in h.probabilities)
    assert h.probabilities[0].up == 1.0 and h.probabilities[0].down == 0.0


def test_bearish_events_are_reported_in_price_frame() -> None:
    series = rising_series()
    history = [item(series, e, direction="bearish") for e in range(20, 150, 10)]
    query = item(series, 160, direction="bearish")

    [h] = empirical_forecast(
        query, history, as_of=FAR, horizons=(4,), min_effective=1
    ).horizons

    # событие-медведь в растущем рынке: в своём направлении −2 ATR, по цене +2
    assert h.median_ret == pytest.approx(2.0)
    assert h.probabilities[0].up == 1.0


def test_filters_group_direction_self_and_future() -> None:
    series = rising_series()
    query = item(series, 100)
    history = [
        item(series, 20),
        item(series, 30, group="double_top"),
        item(series, 40, direction="bearish"),
        query,  # сам запрос не входит в выборку
        item(series, 150),  # позже as_of
    ]
    as_of = series.bars[120].close_time

    result = empirical_forecast(query, history, as_of=as_of, horizons=(3,))

    assert result.sample == 1


def test_same_regime_filter_and_unknown_regime_warning() -> None:
    regimes: list[Regime | None] = [UP if i < 100 else DOWN for i in range(N)]
    series = rising_series(regimes)
    history = [item(series, 20), item(series, 30), item(series, 120)]
    in_up = item(series, 60)
    in_down = item(series, 140)

    up = empirical_forecast(in_up, history, as_of=FAR, same_regime=True)
    down = empirical_forecast(in_down, history, as_of=FAR, same_regime=True)
    everything = empirical_forecast(in_up, history, as_of=FAR)

    assert (up.sample, down.sample, everything.sample) == (2, 1, 3)
    unknown = empirical_forecast(
        item(rising_series(), 60), history, as_of=FAR, same_regime=True
    )
    assert "unknown_regime" in unknown.warnings and unknown.sample == 3


def test_no_history_warns_and_censors_tail() -> None:
    series = rising_series()
    query = item(series, 100)

    empty = empirical_forecast(query, [query], as_of=FAR, horizons=(5,))
    assert empty.sample == 0 and "no_history" in empty.warnings
    assert empty.horizons[0].warnings == ["no_data"]

    tail = [item(series, N - 3)]  # горизонт 5 не помещается — censored
    cut = empirical_forecast(query, tail, as_of=FAR, horizons=(5,))
    assert cut.horizons[0].censored == 1 and cut.horizons[0].n_effective == 0


def test_forecast_does_not_depend_on_bars_after_as_of() -> None:
    clean = rising_series()
    history = [item(clean, e) for e in (20, 60, 100, 145)]
    query = item(clean, 150)
    as_of = clean.bars[150].close_time
    cut = replace(
        clean, bars=clean.bars[:151], atrs=clean.atrs[:151], regimes=clean.regimes[:151]
    )
    cut = replace(cut, index=close_index(cut.bars))
    cut_history = [SeriesOccurrence(cut, i.occurrence) for i in history]
    cut_query = SeriesOccurrence(cut, query.occurrence)

    a = empirical_forecast(query, history, as_of=as_of, horizons=(3, 8))
    b = empirical_forecast(cut_query, cut_history, as_of=as_of, horizons=(3, 8))

    assert a.horizons[0] == b.horizons[0]  # горизонт 3 помещается и там, и там
    # горизонт 8 у входа 145 выходит за as_of: на обрезанном ряду он цензурирован
    assert (a.horizons[1].censored, b.horizons[1].censored) == (0, 1)


def knn_matches(series: Series, entries: list[int]) -> list[AnalogueMatch]:
    query = formation_at(series, 190, window=WINDOW)
    assert query is not None
    items = [item(series, e) for e in entries]
    candidates = build_candidates(items, as_of=FAR, window=WINDOW, exclude=query)
    return find_analogues(query, candidates, k=20, horizons=(4,)).matches


def test_knn_uses_raw_direction_and_matches() -> None:
    series = rising_series()
    # синусоида вокруг роста, чтобы формации были непустыми и различными
    matches = knn_matches(series, [60, 90, 120, 150])

    result = knn_forecast(matches, horizons=(4,), min_effective=1)

    [h] = result.horizons
    assert result.method == "knn" and result.sample == len(matches) > 0
    assert h.median_ret == pytest.approx(2.0)  # рост цены, сырое направление
    assert h.probabilities[0].up == 1.0


def test_knn_without_matches_warns() -> None:
    result = knn_forecast([], horizons=(5,))

    assert result.sample == 0 and result.warnings == ["no_matches"]
    assert result.horizons[0].warnings == ["no_data"]


def test_knn_and_empirical_agree_on_the_same_outcomes() -> None:
    series = rising_series()
    entries = [60, 90, 120, 150]
    matches = knn_matches(series, entries)
    knn = knn_forecast(matches, horizons=(4,), min_effective=1)
    history = [item(series, e) for e in entries]
    emp = empirical_forecast(
        item(series, 190), history, as_of=FAR, horizons=(4,), min_effective=1
    )

    assert knn.horizons[0].median_ret == emp.horizons[0].median_ret
