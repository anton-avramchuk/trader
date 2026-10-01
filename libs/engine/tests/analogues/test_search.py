"""Поиск аналогов: порядок, порог, de-overlap, траектории, перцентили."""

from datetime import timedelta

import pytest

from tests.analogues.test_library import make_series, occurrence
from tests.stats.test_outcomes import START, bar
from trader_engine.analogues.library import (
    Candidate,
    build_candidates,
    formation_at,
)
from trader_engine.analogues.search import find_analogues, percentile, trajectory
from trader_engine.stats.occurrences import close_index
from trader_engine.stats.pipeline import Series

WINDOW = 30


def wave_series(key: str, amplitude: float, n: int = 200) -> Series:
    bars = [
        bar(i, 100.0 + amplitude * ((i % 10) - 5) ** 2 / 25 + 0.5 * (i // 40))
        for i in range(n)
    ]
    return Series(key, bars, [2.0] * n, [None] * n, close_index(bars))


def candidates(series: Series, entries: list[int]) -> list[Candidate]:
    return build_candidates(
        [occurrence(series, e) for e in entries],
        as_of=START + timedelta(days=3650),
        window=WINDOW,
    )


def test_identical_series_ranks_closest_first() -> None:
    same = wave_series("A", 4.0)
    other = wave_series("B", 12.0)
    query = formation_at(same, 120, window=WINDOW)
    assert query is not None
    pool = candidates(same, [60, 90]) + candidates(other, [60, 90])
    result = find_analogues(query, pool, k=10, horizons=(5,))
    assert result.matches[0].candidate.formation.series_key == "A"
    distances = [m.dtw.normalized_distance for m in result.matches]
    assert distances == sorted(distances)


def test_k_limits_and_max_distance_filters() -> None:
    series = wave_series("A", 4.0)
    query = formation_at(series, 150, window=WINDOW)
    assert query is not None
    pool = candidates(series, [40, 70, 100])
    assert len(find_analogues(query, pool, k=2, horizons=(5,)).matches) == 2
    assert find_analogues(query, pool, max_distance=-1.0, horizons=(5,)).matches == []


def test_deoverlap_keeps_closest_in_same_series() -> None:
    series = wave_series("A", 4.0)
    query = formation_at(series, 150, window=WINDOW)
    assert query is not None
    pool = candidates(series, [60, 70, 100])  # 60 и 70 ближе окна 30
    result = find_analogues(query, pool, horizons=(5,))
    entries = sorted(m.candidate.entry_index for m in result.matches)
    assert len(entries) == 2 and 100 in entries


def test_trajectory_in_atr_and_pct_and_censoring() -> None:
    series = make_series()
    [cand] = candidates(series, [50])
    path = trajectory(cand, 3, "atr")
    assert path is not None and len(path) == 3
    base = series.bars[50].close
    assert path[0] == pytest.approx(series.bars[51].close - base)
    pct = trajectory(cand, 2, "pct")
    assert pct is not None
    assert pct[1] == pytest.approx((series.bars[52].close / base - 1) * 100)
    assert trajectory(cand, 500, "atr") is None


def test_percentile_interpolates() -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == pytest.approx(2.5)
    assert percentile([5.0], 25) == 5.0
    with pytest.raises(ValueError):
        percentile([], 50)


def test_percentile_bands_and_warnings() -> None:
    series = wave_series("A", 4.0)
    query = formation_at(series, 150, window=WINDOW)
    assert query is not None
    result = find_analogues(query, candidates(series, [60, 100]), horizons=(5, 10))
    assert set(result.percentiles) == {25, 50, 75}
    assert all(len(v) == 10 for v in result.percentiles.values())
    low, mid, high = (result.percentiles[q] for q in (25, 50, 75))
    assert all(a <= b <= c for a, b, c in zip(low, mid, high, strict=True))
    assert "few_matches" in result.warnings
    assert result.stats.matched == len(result.matches)


def test_no_candidates_warns() -> None:
    series = wave_series("A", 4.0)
    query = formation_at(series, 150, window=WINDOW)
    assert query is not None
    result = find_analogues(query, [], horizons=(5,))
    assert result.matches == [] and result.warnings == ["no_candidates"]
    assert result.percentiles == {}


def test_invalid_k_rejected() -> None:
    series = wave_series("A", 4.0)
    query = formation_at(series, 150, window=WINDOW)
    assert query is not None
    with pytest.raises(ValueError):
        find_analogues(query, [], k=0)
