"""Библиотека кандидатов: окна, причинность, исключение окна запроса."""

from datetime import datetime, timedelta

import pytest

from tests.stats.test_outcomes import START, bar
from trader_engine.analogues.library import build_candidates, formation_at
from trader_engine.stats.occurrences import Occurrence, close_index
from trader_engine.stats.pipeline import Series, SeriesOccurrence


def make_series(key: str = "S", n: int = 120) -> Series:
    bars = [bar(i, 100.0 + (i % 7) * 2.0 + i * 0.1) for i in range(n)]
    return Series(
        key=key,
        bars=bars,
        atrs=[1.0] * n,
        regimes=[None] * n,
        index=close_index(bars),
    )


def occurrence(series: Series, entry: int, key: str = "e") -> SeriesOccurrence:
    return SeriesOccurrence(
        series,
        Occurrence(
            key=f"{series.key}:{key}{entry}",
            engine="x",
            kind="pattern",
            group="double_top",
            direction="bullish",
            entry="confirmed",
            available_at=series.bars[entry].close_time,
        ),
    )


def far_future() -> datetime:
    return START + timedelta(days=3650)


def test_formation_window_and_shape() -> None:
    series = make_series()
    formation = formation_at(series, 59, window=60, k=7)
    assert formation is not None
    assert (formation.start, formation.end) == (0, 59)
    assert len(formation.shape.values) == 7


def test_formation_needs_full_window_and_atr() -> None:
    series = make_series()
    assert formation_at(series, 58, window=60) is None
    no_atr = Series(
        series.key, series.bars, [None] * len(series.bars), series.regimes, series.index
    )
    assert formation_at(no_atr, 80, window=60) is None


def test_small_window_rejected() -> None:
    with pytest.raises(ValueError):
        formation_at(make_series(), 50, window=5)


def test_future_occurrences_are_excluded_by_as_of() -> None:
    series = make_series()
    items = [occurrence(series, 70), occurrence(series, 100)]
    as_of = series.bars[80].close_time
    result = build_candidates(items, as_of=as_of, window=30)
    assert [c.entry_index for c in result] == [70]


def test_query_window_and_overlapping_candidates_excluded() -> None:
    series = make_series()
    query = formation_at(series, 100, window=30)
    assert query is not None
    items = [occurrence(series, 50), occurrence(series, 75), occurrence(series, 100)]
    result = build_candidates(items, as_of=far_future(), window=30, exclude=query)
    assert [c.entry_index for c in result] == [50]


def test_other_series_not_excluded_by_query_window() -> None:
    a, b = make_series("A"), make_series("B")
    query = formation_at(a, 100, window=30)
    assert query is not None
    result = build_candidates(
        [occurrence(b, 100)], as_of=far_future(), window=30, exclude=query
    )
    assert len(result) == 1


def test_occurrence_without_entry_bar_or_history_is_skipped() -> None:
    series = make_series()
    early = occurrence(series, 10)
    ghost = SeriesOccurrence(
        series,
        Occurrence(
            key="g",
            engine="x",
            kind="level",
            group="level_touch",
            direction="bearish",
            entry="touch",
            available_at=START - timedelta(days=1),
        ),
    )
    assert build_candidates([early, ghost], as_of=far_future(), window=30) == []


def test_result_is_ordered_by_time() -> None:
    series = make_series()
    items = [occurrence(series, 90), occurrence(series, 40), occurrence(series, 65)]
    result = build_candidates(items, as_of=far_future(), window=30)
    assert [c.entry_index for c in result] == [40, 65, 90]
