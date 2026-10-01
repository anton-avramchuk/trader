"""Сборка статистики: ряды, вхождения, фильтры, группы, baseline (ADR-0023).

Чистый код без ввода-вывода: API читает бары и события, строит ``Series`` и вызывает
``compute_statistics``. Серия — один ряд баров (инструмент и TF) со своим ATR,
режимом и роллами; вхождения приходят из логов движков этого ряда.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from trader_engine.events import create, run_engine
from trader_engine.indicators.base import BarInput
from trader_engine.stats.aggregate import (
    MIN_BASELINE,
    HorizonStats,
    Observation,
    Unit,
    summarize,
)
from trader_engine.stats.baseline import (
    DEFAULT_PER_EVENT,
    BaselineRequest,
    sample_baseline,
)
from trader_engine.stats.occurrences import (
    EventLike,
    Occurrence,
    close_index,
    level_occurrences,
    pattern_occurrences,
)
from trader_engine.stats.outcomes import (
    DEFAULT_ATR_PERIOD,
    DEFAULT_HORIZONS,
    HorizonOutcome,
    atr_series,
    compute_outcomes,
)
from trader_engine.stats.regime import Regime, regime_series

GroupBy = str  # "none" | "group" | "direction" | "regime"
GROUP_BYS = ("none", "group", "direction", "regime")
UNKNOWN_REGIME = "unknown"
SERIES_STRIDE = 10**9  # сдвиг индекса баров по рядам: порядок и независимость рядов


@dataclass(frozen=True)
class Series:
    """Ряд баров с предрассчитанными ATR, режимом и индексом времени закрытия."""

    key: str
    bars: Sequence[BarInput]
    atrs: Sequence[float | None]
    regimes: Sequence[Regime | None]
    index: dict[datetime, int]


def build_series(
    key: str,
    bars: Sequence[BarInput],
    *,
    as_of: datetime | None = None,
    atr_period: int = DEFAULT_ATR_PERIOD,
) -> Series:
    """Ряд для статистики; ``as_of`` отсекает бары, которых на тот момент не было."""
    known = [b for b in bars if as_of is None or b.close_time <= as_of]
    atrs = atr_series(known, atr_period)
    trend = run_engine(create("market_structure"), list(known))
    return Series(
        key=key,
        bars=known,
        atrs=atrs,
        regimes=regime_series(known, atrs, trend),
        index=close_index(known),
    )


@dataclass(frozen=True)
class Filters:
    """Фильтры выборки; пустое значение — не фильтровать."""

    groups: tuple[str, ...] = ()
    direction: str | None = None
    final_states: tuple[str, ...] = ()
    false_breakout: bool | None = None
    quality_min: float | None = None
    quality_max: float | None = None
    trends: tuple[str, ...] = ()
    volatilities: tuple[str, ...] = ()
    since: datetime | None = None
    until: datetime | None = None


@dataclass(frozen=True)
class SeriesOccurrence:
    series: Series
    occurrence: Occurrence

    @property
    def entry_index(self) -> int | None:
        return self.series.index.get(self.occurrence.available_at)

    @property
    def regime(self) -> Regime | None:
        entry = self.entry_index
        return None if entry is None else self.series.regimes[entry]


def collect(
    series: Series,
    engine_events: Iterable[tuple[str, Sequence[EventLike]]],
    *,
    include_candidates: bool = False,
) -> list[SeriesOccurrence]:
    """Вхождения ряда из логов его движков: ``(имя движка, события)``."""
    found: list[SeriesOccurrence] = []
    for engine, events in engine_events:
        for occurrence in (
            *pattern_occurrences(engine, events, include_candidates=include_candidates),
            *level_occurrences(engine, events),
        ):
            found.append(SeriesOccurrence(series, occurrence))
    return found


def regime_label(regime: Regime | None) -> str:
    return UNKNOWN_REGIME if regime is None else regime.label


def matches(item: SeriesOccurrence, filters: Filters) -> bool:
    o = item.occurrence
    regime = item.regime
    if item.entry_index is None:
        return False
    if filters.groups and o.group not in filters.groups:
        return False
    if filters.direction and o.direction != filters.direction:
        return False
    if filters.final_states and (o.final_state or o.entry) not in filters.final_states:
        return False
    if (
        filters.false_breakout is not None
        and o.false_breakout != filters.false_breakout
    ):
        return False
    if filters.quality_min is not None and (o.quality or 0.0) < filters.quality_min:
        return False
    if filters.quality_max is not None and (o.quality or 0.0) > filters.quality_max:
        return False
    if filters.trends and (regime is None or regime.trend not in filters.trends):
        return False
    if filters.volatilities and (
        regime is None or regime.volatility not in filters.volatilities
    ):
        return False
    if filters.since and o.available_at < filters.since:
        return False
    return not (filters.until and o.available_at > filters.until)


@dataclass(frozen=True)
class BucketStats:
    key: str
    n_occurrences: int
    horizons: list[HorizonStats]


@dataclass(frozen=True)
class StatsResult:
    matched: int
    unit: Unit
    buckets: list[BucketStats] = field(default_factory=list[BucketStats])
    warnings: list[str] = field(default_factory=list[str])


def _bucket_key(item: SeriesOccurrence, group_by: GroupBy) -> str:
    if group_by == "group":
        return item.occurrence.group
    if group_by == "direction":
        return item.occurrence.direction
    if group_by == "regime":
        return regime_label(item.regime)
    return "all"


def _offset(ordinal: int, index: int) -> int:
    return ordinal * SERIES_STRIDE + index


def compute_statistics(
    items: Sequence[SeriesOccurrence],
    *,
    filters: Filters | None = None,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    unit: Unit = "atr",
    group_by: GroupBy = "none",
    baseline: bool = True,
    per_event: int = DEFAULT_PER_EVENT,
    seed: int = 0,
    min_baseline: int = MIN_BASELINE,
) -> StatsResult:
    """Статистика по отфильтрованным вхождениям, по горизонтам, с baseline."""
    active = filters or Filters()
    chosen = [item for item in items if matches(item, active)]
    ordinals: dict[str, int] = {}
    for item in items:
        ordinals.setdefault(item.series.key, len(ordinals))
    buckets: dict[str, list[SeriesOccurrence]] = {}
    for item in chosen:
        buckets.setdefault(_bucket_key(item, group_by), []).append(item)
    result: list[BucketStats] = []
    for key in sorted(buckets):
        members = buckets[key]
        baseline_outcomes = _baseline(
            members, ordinals, horizons, baseline, per_event, seed
        )
        stats: list[HorizonStats] = []
        for horizon in horizons:
            observations = _observations(members, ordinals, horizon)
            horizon_stats = summarize(
                observations,
                baseline_outcomes.get(horizon, []),
                horizon=horizon,
                unit=unit,
                seed=seed,
                min_baseline=min_baseline,
            )
            if not baseline and "no_baseline" in horizon_stats.warnings:
                horizon_stats.warnings.remove("no_baseline")  # не запрашивали
            stats.append(horizon_stats)
        result.append(BucketStats(key, len(members), stats))
    return StatsResult(len(chosen), unit, result)


def _observations(
    members: Sequence[SeriesOccurrence], ordinals: dict[str, int], horizon: int
) -> list[Observation]:
    found: list[Observation] = []
    for item in members:
        entry = item.entry_index
        if entry is None:
            continue
        outcome = _outcome(item, entry, horizon)
        found.append(
            Observation(
                (item.series.key, item.occurrence.direction),
                _offset(ordinals[item.series.key], entry),
                outcome,
            )
        )
    return found


def _outcome(item: SeriesOccurrence, entry: int, horizon: int) -> HorizonOutcome:
    series, o = item.series, item.occurrence
    return compute_outcomes(
        series.bars,
        entry,
        o.direction,
        series.atrs[entry],
        target=o.target,
        invalidated_at=o.invalidated_at,
        horizons=(horizon,),
    )[0]


def _baseline(
    members: Sequence[SeriesOccurrence],
    ordinals: dict[str, int],
    horizons: Sequence[int],
    enabled: bool,
    per_event: int,
    seed: int,
) -> dict[int, list[Observation]]:
    if not enabled or not horizons:
        return {}
    by_series: dict[str, list[SeriesOccurrence]] = {}
    for item in members:
        by_series.setdefault(item.series.key, []).append(item)
    found: dict[int, list[Observation]] = {h: [] for h in horizons}
    for key, group in by_series.items():
        series = group[0].series
        requests = [
            BaselineRequest(item.entry_index, item.occurrence.direction)
            for item in group
            if item.entry_index is not None
        ]
        for horizon in horizons:
            # окно исключения — этот же горизонт: у плотных событий (уровни) окно
            # наибольшего горизонта закрыло бы весь ряд
            points = sample_baseline(
                series.regimes,
                requests,
                exclusion=horizon,
                per_event=per_event,
                seed=seed,
            )
            for point in points:
                outcome = compute_outcomes(
                    series.bars,
                    point.index,
                    point.direction,
                    series.atrs[point.index],
                    horizons=(horizon,),
                )[0]
                found[horizon].append(
                    Observation(
                        (key, "baseline"),
                        _offset(ordinals[key], point.index),
                        outcome,
                    )
                )
    return found


@dataclass(frozen=True)
class OccurrenceDetail:
    occurrence: Occurrence
    regime: str
    outcomes: list[HorizonOutcome]


def occurrence_detail(
    item: SeriesOccurrence, horizons: Sequence[int] = DEFAULT_HORIZONS
) -> OccurrenceDetail | None:
    """Исходы одного вхождения по горизонтам; ``None``, если бара входа нет."""
    entry = item.entry_index
    if entry is None:
        return None
    return OccurrenceDetail(
        item.occurrence,
        regime_label(item.regime),
        [_outcome(item, entry, horizon) for horizon in horizons],
    )
