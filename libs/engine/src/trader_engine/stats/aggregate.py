"""Агрегаты исходов: de-overlap, эффективный N, block bootstrap CI (ADR-0023).

Сырой N завышает уверенность: события ближе друг к другу, чем горизонт, делят одни
и те же бары исхода. Поэтому перед агрегацией события одного ряда (инструмент, TF,
направление) прореживаются окном, равным горизонту, а доверительные интервалы
считаются блочным бутстрепом по времени — он сохраняет зависимость соседних
наблюдений. Всё детерминировано при фиксированном ``seed``.
"""

import random
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass, field
from statistics import mean, median
from typing import Literal

from trader_engine.stats.outcomes import HorizonOutcome

Unit = Literal["atr", "pct"]

MIN_EFFECTIVE = 30
MIN_BASELINE = 20
N_BOOT = 1000
CI_LEVEL = 0.95


@dataclass(frozen=True, slots=True)
class Observation:
    """Исход вхождения на горизонте; ``key`` — ряд (инструмент, TF, направление)."""

    key: Hashable
    index: int
    outcome: HorizonOutcome


@dataclass(frozen=True, slots=True)
class Dist:
    mean: float
    median: float


@dataclass(frozen=True, slots=True)
class Interval:
    low: float
    high: float


@dataclass(frozen=True, slots=True)
class HorizonStats:
    """Статистика на одном горизонте; числовые поля ``None``, если данных нет."""

    horizon: int
    unit: Unit
    n_raw: int
    n_effective: int
    censored: int
    missing_atr: int
    win_rate: float | None = None
    ret: Dist | None = None
    mfe: Dist | None = None
    mae: Dist | None = None
    ret_ci: Interval | None = None
    target_rate: float | None = None
    invalidated_rate: float | None = None
    baseline_n: int = 0
    baseline_ret: Dist | None = None
    edge: float | None = None
    edge_ci: Interval | None = None
    flags: dict[str, int] = field(default_factory=dict[str, int])
    warnings: list[str] = field(default_factory=list[str])


def deoverlap(items: Sequence[tuple[Hashable, int]], window: int) -> list[int]:
    """Позиции, оставшиеся после прореживания: в каждом ряду — первое событие, затем
    только те, что не ближе ``window`` баров к последнему оставленному."""
    groups: dict[Hashable, list[int]] = {}
    for position, (key, _) in enumerate(items):
        groups.setdefault(key, []).append(position)
    kept: list[int] = []
    for positions in groups.values():
        last: int | None = None
        for position in sorted(positions, key=lambda p: (items[p][1], p)):
            index = items[position][1]
            if last is None or index - last >= window:
                kept.append(position)
                last = index
    return sorted(kept)


def _boot_means(
    values: Sequence[float], n_boot: int, block: int, rng: random.Random
) -> list[float]:
    """Средние ``n_boot`` ресэмплов: круговые блоки подряд идущих значений."""
    n = len(values)
    prefix = [0.0]
    for value in (*values, *values):
        prefix.append(prefix[-1] + value)
    full, rest = divmod(n, block)
    lengths = [block] * full + ([rest] if rest else [])
    means: list[float] = []
    for _ in range(n_boot):
        total = 0.0
        for length in lengths:
            start = rng.randrange(n)
            total += prefix[start + length] - prefix[start]
        means.append(total / n)
    return means


def _interval(samples: list[float], level: float) -> Interval:
    ordered = sorted(samples)
    last = len(ordered) - 1
    low = int((1 - level) / 2 * len(ordered))
    high = min(int((1 + level) / 2 * len(ordered)), last)
    return Interval(ordered[low], ordered[high])


def _block(n: int, block: int | None) -> int:
    return block if block else max(1, round(n ** (1 / 3)))


def bootstrap_ci(
    values: Sequence[float],
    *,
    n_boot: int = N_BOOT,
    level: float = CI_LEVEL,
    block: int | None = None,
    seed: int = 0,
) -> Interval | None:
    """CI среднего блочным бутстрепом (значения по времени); мало данных — ``None``."""
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    means = _boot_means(values, n_boot, _block(len(values), block), rng)
    return _interval(means, level)


def bootstrap_diff_ci(
    a: Sequence[float],
    b: Sequence[float],
    *,
    n_boot: int = N_BOOT,
    level: float = CI_LEVEL,
    seed: int = 0,
) -> Interval | None:
    """CI разности средних ``mean(a) - mean(b)``: выборки ресэмплируются независимо."""
    if len(a) < 2 or len(b) < 2:
        return None
    rng = random.Random(seed)
    first = _boot_means(a, n_boot, _block(len(a), None), rng)
    second = _boot_means(b, n_boot, _block(len(b), None), rng)
    return _interval([x - y for x, y in zip(first, second, strict=True)], level)


def _pick(
    unit: Unit,
) -> tuple[
    Callable[[HorizonOutcome], float | None],
    Callable[[HorizonOutcome], float | None],
    Callable[[HorizonOutcome], float | None],
]:
    if unit == "atr":
        return (lambda o: o.ret_atr, lambda o: o.mfe_atr, lambda o: o.mae_atr)
    return (lambda o: o.ret_pct, lambda o: o.mfe_pct, lambda o: o.mae_pct)


def _dist(values: Sequence[float]) -> Dist | None:
    return Dist(mean(values), median(values)) if values else None


def summarize(
    observations: Sequence[Observation],
    baseline: Sequence[Observation] = (),
    *,
    horizon: int,
    unit: Unit = "atr",
    seed: int = 0,
    n_boot: int = N_BOOT,
    min_effective: int = MIN_EFFECTIVE,
    min_baseline: int = MIN_BASELINE,
) -> HorizonStats:
    """Статистика на горизонте: прореживание, агрегаты, CI, сравнение с baseline."""
    ready = [o for o in observations if not o.outcome.censored]
    censored = len(observations) - len(ready)
    kept = deoverlap([(o.key, o.index) for o in ready], horizon)
    effective = sorted((ready[p] for p in kept), key=lambda o: o.index)
    get_ret, get_mfe, get_mae = _pick(unit)
    usable = [o for o in effective if get_ret(o.outcome) is not None]
    missing = len(effective) - len(usable)
    warnings: list[str] = []
    if not effective:
        warnings.append("no_data")
    elif len(effective) < min_effective:
        warnings.append("small_sample")
    if missing:
        warnings.append("missing_atr")

    returns = [float(v) for o in usable if (v := get_ret(o.outcome)) is not None]
    mfes = [float(v) for o in usable if (v := get_mfe(o.outcome)) is not None]
    maes = [float(v) for o in usable if (v := get_mae(o.outcome)) is not None]
    outcomes = [o.outcome for o in effective]
    base_returns = [
        float(v)
        for o in sorted(baseline, key=lambda o: o.index)
        if not o.outcome.censored and (v := get_ret(o.outcome)) is not None
    ]
    edge = edge_ci = None
    if returns and len(base_returns) >= min_baseline:
        edge = mean(returns) - mean(base_returns)
        edge_ci = bootstrap_diff_ci(returns, base_returns, n_boot=n_boot, seed=seed)
    elif returns:
        # мало случайных точек — сравнение ничего не стоит, даже с узким CI
        warnings.append("small_baseline" if base_returns else "no_baseline")
    total = len(outcomes)
    return HorizonStats(
        horizon=horizon,
        unit=unit,
        n_raw=len(ready),
        n_effective=len(effective),
        censored=censored,
        missing_atr=missing,
        win_rate=sum(r > 0 for r in returns) / len(returns) if returns else None,
        ret=_dist(returns),
        mfe=_dist(mfes),
        mae=_dist(maes),
        ret_ci=bootstrap_ci(returns, n_boot=n_boot, seed=seed),
        target_rate=sum(o.first_hit == "target" for o in outcomes) / total
        if total
        else None,
        invalidated_rate=sum(o.first_hit == "invalidated" for o in outcomes) / total
        if total
        else None,
        baseline_n=len(base_returns),
        baseline_ret=_dist(base_returns),
        edge=edge,
        edge_ci=edge_ci,
        flags={
            "crosses_session_gap": sum(o.crosses_session_gap for o in outcomes),
            "crosses_weekend": sum(o.crosses_weekend for o in outcomes),
            "ambiguous_bar": sum(o.ambiguous_bar for o in outcomes),
        },
        warnings=warnings,
    )
