"""Walk-forward калибровка Empirical-прогноза (ADR-0026).

Для каждого исторического вхождения прогноз строится только по тому, что было
известно к его входу: по более ранним вхождениям того же типа и направления, чей
исход на горизонте уже закрылся (бар ``entry + horizon`` закрыт не позже входа
проверяемого). Предсказанные вероятности сравниваются с реализованным исходом:
Brier score, reliability-таблица (корзины вероятностей против частоты, интервал
Уилсона) и сравнение с «климатологией» — константой, равной общей частоте в
проверяемой выборке (навык = 1 − Brier / Brier климатологии; около нуля — прогноз
не лучше безусловной частоты). Исходы — в направлении события.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from math import sqrt
from typing import Literal

from trader_engine.forecast.core import THRESHOLDS, forecast_horizon
from trader_engine.stats.aggregate import MIN_EFFECTIVE, Observation, Unit
from trader_engine.stats.outcomes import compute_outcomes
from trader_engine.stats.pipeline import SeriesOccurrence

BINS = 5
MIN_HISTORY = 20
Z95 = 1.96
Side = Literal["up", "down"]


@dataclass(frozen=True, slots=True)
class ReliabilityBin:
    """Корзина предсказанных вероятностей: средний прогноз и наблюдаемая частота."""

    low: float
    high: float
    n: int
    mean_predicted: float | None
    observed: float | None
    observed_low: float | None = None
    observed_high: float | None = None


@dataclass(frozen=True, slots=True)
class ThresholdCalibration:
    threshold: float
    side: Side
    n: int
    brier: float
    brier_climatology: float
    skill: float | None
    bins: list[ReliabilityBin] = field(default_factory=list[ReliabilityBin])


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    horizon: int
    unit: Unit
    tested: int
    skipped: int
    rows: list[ThresholdCalibration]
    warnings: list[str] = field(default_factory=list[str])


def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Интервал Уилсона для доли."""
    if n == 0:
        raise ValueError("n должно быть положительным")
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def _bins(pairs: Sequence[tuple[float, bool]]) -> list[ReliabilityBin]:
    result: list[ReliabilityBin] = []
    for index in range(BINS):
        low, high = index / BINS, (index + 1) / BINS
        members = [
            (p, hit)
            for p, hit in pairs
            if low <= p < high or (index == BINS - 1 and p == 1.0)
        ]
        if not members:
            result.append(ReliabilityBin(low, high, 0, None, None))
            continue
        hits = sum(hit for _, hit in members)
        ci_low, ci_high = wilson(hits, len(members))
        result.append(
            ReliabilityBin(
                low,
                high,
                len(members),
                sum(p for p, _ in members) / len(members),
                hits / len(members),
                ci_low,
                ci_high,
            )
        )
    return result


def _calibration(
    threshold: float, side: Side, pairs: Sequence[tuple[float, bool]]
) -> ThresholdCalibration:
    n = len(pairs)
    brier = sum((p - hit) ** 2 for p, hit in pairs) / n
    frequency = sum(hit for _, hit in pairs) / n
    climatology = frequency * (1 - frequency)
    return ThresholdCalibration(
        threshold,
        side,
        n,
        brier,
        climatology,
        None if climatology == 0 else 1 - brier / climatology,
        _bins(pairs),
    )


def calibrate(
    items: Sequence[SeriesOccurrence],
    *,
    horizon: int,
    unit: Unit = "atr",
    thresholds: Sequence[float] = THRESHOLDS,
    min_history: int = MIN_HISTORY,
    until: datetime | None = None,
) -> CalibrationReport:
    """Калибровка по вхождениям одного типа и направления (их отбирает вызывающий)."""
    known: list[tuple[datetime, datetime, Observation]] = []
    for item in sorted(items, key=lambda i: i.occurrence.available_at):
        entry = item.entry_index
        if entry is None:
            continue
        series, occurrence = item.series, item.occurrence
        outcome = compute_outcomes(
            series.bars,
            entry,
            occurrence.direction,
            series.atrs[entry],
            horizons=(horizon,),
        )[0]
        if outcome.censored or entry + horizon >= len(series.bars):
            continue
        ends = series.bars[entry + horizon].close_time
        known.append(
            (
                occurrence.available_at,
                ends,
                Observation((series.key, occurrence.direction), entry, outcome),
            )
        )
    pairs: dict[tuple[float, Side], list[tuple[float, bool]]] = {
        (k, side): [] for k in thresholds for side in ("up", "down")
    }
    tested = skipped = 0
    for at, _, current in known:
        if until is not None and at > until:
            break
        ret = current.outcome.ret_atr if unit == "atr" else current.outcome.ret_pct
        history = [o for start, end, o in known if start < at and end <= at]
        if ret is None or len(history) < min_history:
            skipped += 1
            continue
        forecast = forecast_horizon(
            history,
            horizon=horizon,
            unit=unit,
            thresholds=thresholds,
            min_effective=min_history,
            ci=False,
        )
        if forecast.n_effective < min_history or not forecast.probabilities:
            skipped += 1
            continue
        tested += 1
        for p in forecast.probabilities:
            pairs[(p.threshold, "up")].append((p.up, ret >= p.threshold))
            pairs[(p.threshold, "down")].append((p.down, ret <= -p.threshold))
    rows = [
        _calibration(k, side, found)  # type: ignore[arg-type]
        for (k, side), found in pairs.items()
        if found
    ]
    warnings: list[str] = []
    if not tested:
        warnings.append("not_enough_history")
    elif tested < MIN_EFFECTIVE:
        warnings.append("small_sample")
    return CalibrationReport(horizon, unit, tested, skipped, rows, warnings)
