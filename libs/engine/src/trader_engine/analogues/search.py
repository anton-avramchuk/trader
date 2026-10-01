"""Поиск аналогов (ADR-0025): DTW по кандидатам, Top-K, de-overlap, исходы.

Исходы аналогов считаются в «сыром» направлении (рост цены — плюс): у запроса в
режиме окна направления нет, а форма уже несёт направление (двойная вершина и
двойное дно — разные формы). Статистика — та же, что в MVP-5 (эффективный N,
bootstrap CI, baseline): аналоги подаются как вхождения «bullish» без цели.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from math import floor

from trader_engine.analogues.dtw import (
    DEFAULT_BAND,
    DEFAULT_TIME_WEIGHT,
    DtwResult,
    dtw,
)
from trader_engine.analogues.library import Candidate, Formation
from trader_engine.stats.aggregate import Unit
from trader_engine.stats.outcomes import (
    DEFAULT_HORIZONS,
    HorizonOutcome,
    compute_outcomes,
)
from trader_engine.stats.pipeline import (
    SeriesOccurrence,
    StatsResult,
    compute_statistics,
)

DEFAULT_K = 20
PERCENTILES: tuple[int, ...] = (25, 50, 75)
MIN_MATCHES = 5


@dataclass(frozen=True, slots=True)
class AnalogueMatch:
    """Один аналог: кандидат, расстояние, исходы и траектория после входа."""

    candidate: Candidate
    dtw: DtwResult
    outcomes: list[HorizonOutcome]
    trajectory: list[float] | None


@dataclass(frozen=True, slots=True)
class AnalogueResult:
    considered: int
    matches: list[AnalogueMatch]
    stats: StatsResult
    percentiles: dict[int, list[float]] = field(default_factory=dict[int, list[float]])
    trajectory_count: int = 0
    warnings: list[str] = field(default_factory=list[str])


def percentile(values: Sequence[float], q: float) -> float:
    """Перцентиль ``q`` (0–100) с линейной интерполяцией."""
    if not values:
        raise ValueError("пустой набор")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q / 100.0
    low = floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def trajectory(
    candidate: Candidate, steps: int, unit: Unit = "atr"
) -> list[float] | None:
    """Close после входа на каждом из ``steps`` баров: сдвиг от входа в ATR или %."""
    series, entry = candidate.series, candidate.entry_index
    if steps < 1 or entry + steps >= len(series.bars):
        return None
    base = series.bars[entry].close
    closes = [series.bars[entry + s].close for s in range(1, steps + 1)]
    if unit == "atr":
        atr = series.atrs[entry]
        if atr is None or atr <= 0:
            return None
        return [(close - base) / atr for close in closes]
    if base == 0:
        return None
    return [(close / base - 1.0) * 100.0 for close in closes]


def find_analogues(
    query: Formation,
    candidates: Sequence[Candidate],
    *,
    k: int = DEFAULT_K,
    max_distance: float | None = None,
    band: float = DEFAULT_BAND,
    time_weight: float = DEFAULT_TIME_WEIGHT,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    unit: Unit = "atr",
    seed: int = 0,
) -> AnalogueResult:
    """Ближайшие аналоги запроса, их исходы, траектории и агрегат."""
    if k < 1:
        raise ValueError("k должно быть положительным")
    scored: list[tuple[Candidate, DtwResult]] = []
    for candidate in candidates:
        result = dtw(query.shape, candidate.formation.shape, band, time_weight)
        if result is None:
            continue
        if max_distance is not None and result.normalized_distance > max_distance:
            continue
        scored.append((candidate, result))
    scored.sort(key=lambda pair: (pair[1].normalized_distance, pair[0].occurrence.key))
    chosen = _deoverlap(scored, query.end - query.start + 1)[:k]

    steps = max(horizons) if horizons else 0
    matches = [
        AnalogueMatch(
            candidate,
            result,
            _raw_outcomes(candidate, horizons),
            trajectory(candidate, steps, unit),
        )
        for candidate, result in chosen
    ]
    stats = compute_statistics(
        [_as_raw(m.candidate) for m in matches],
        horizons=horizons,
        unit=unit,
        seed=seed,
    )
    paths = [m.trajectory for m in matches if m.trajectory is not None]
    bands = {
        q: [percentile([p[s] for p in paths], q) for s in range(steps)]
        for q in PERCENTILES
        if paths
    }
    warnings: list[str] = []
    if not candidates:
        warnings.append("no_candidates")
    elif len(matches) < MIN_MATCHES:
        warnings.append("few_matches")
    return AnalogueResult(len(candidates), matches, stats, bands, len(paths), warnings)


def _deoverlap(
    scored: Sequence[tuple[Candidate, DtwResult]], window: int
) -> list[tuple[Candidate, DtwResult]]:
    """Остаётся ближайший: аналоги одного ряда ближе ``window`` баров — один."""
    kept: list[tuple[Candidate, DtwResult]] = []
    for candidate, result in scored:
        clash = any(
            other.formation.series_key == candidate.formation.series_key
            and abs(other.entry_index - candidate.entry_index) < window
            for other, _ in kept
        )
        if not clash:
            kept.append((candidate, result))
    return kept


def _as_raw(candidate: Candidate) -> SeriesOccurrence:
    raw = replace(
        candidate.occurrence, direction="bullish", target=None, invalidated_at=None
    )
    return SeriesOccurrence(candidate.series, raw)


def _raw_outcomes(
    candidate: Candidate, horizons: Sequence[int]
) -> list[HorizonOutcome]:
    series, entry = candidate.series, candidate.entry_index
    return compute_outcomes(
        series.bars,
        entry,
        "bullish",
        series.atrs[entry],
        roll_times=series.roll_times,
        horizons=horizons,
    )
