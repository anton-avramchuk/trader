"""Ядро Forecast (ADR-0026): из набора исходов — вероятности порогов и квантили.

Вход — исходы на одном горизонте (``Observation`` из MVP-5): неизвестные (censored)
отбрасываются, остальные прореживаются окном = горизонту (эффективный N). По ним:
вероятности ``P(ret ≥ +k)`` и ``P(ret ≤ −k)`` для порогов ``k`` в единицах прогноза
(ATR или %), квантили дохода, медианы MFE/MAE и 95% CI (блочный bootstrap).
Направление исходов задаёт вызывающий: ядро ничего не знает о методе.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from statistics import mean, median

from trader_engine.analogues.search import percentile
from trader_engine.stats.aggregate import (
    MIN_EFFECTIVE,
    Interval,
    Observation,
    Unit,
    bootstrap_ci,
    deoverlap,
)
from trader_engine.stats.outcomes import HorizonOutcome

THRESHOLDS: tuple[float, ...] = (0.5, 1.0, 2.0)
QUANTILES: tuple[int, ...] = (10, 25, 75, 90)


@dataclass(frozen=True, slots=True)
class ThresholdProbability:
    """Вероятности превысить порог вверх и вниз с 95% CI."""

    threshold: float
    up: float
    down: float
    up_ci: Interval | None
    down_ci: Interval | None


@dataclass(frozen=True, slots=True)
class HorizonForecast:
    horizon: int
    unit: Unit
    n_raw: int
    n_effective: int
    censored: int
    missing_atr: int
    mean_ret: float | None = None
    median_ret: float | None = None
    ret_ci: Interval | None = None
    quantiles: dict[int, float] = field(default_factory=dict[int, float])
    median_mfe: float | None = None
    median_mae: float | None = None
    probabilities: list[ThresholdProbability] = field(
        default_factory=list[ThresholdProbability]
    )
    warnings: list[str] = field(default_factory=list[str])


def _values(outcome: HorizonOutcome, unit: Unit) -> tuple[float, float, float] | None:
    if unit == "atr":
        ret, mfe, mae = outcome.ret_atr, outcome.mfe_atr, outcome.mae_atr
    else:
        ret, mfe, mae = outcome.ret_pct, outcome.mfe_pct, outcome.mae_pct
    if ret is None or mfe is None or mae is None:
        return None
    return float(ret), float(mfe), float(mae)


def forecast_horizon(
    observations: Sequence[Observation],
    *,
    horizon: int,
    unit: Unit = "atr",
    thresholds: Sequence[float] = THRESHOLDS,
    seed: int = 0,
    min_effective: int = MIN_EFFECTIVE,
) -> HorizonForecast:
    """Прогноз на горизонте по набору исходов; мало данных — только предупреждения."""
    ready = [o for o in observations if not o.outcome.censored]
    censored = len(observations) - len(ready)
    kept = deoverlap([(o.key, o.index) for o in ready], horizon)
    effective = sorted((ready[p] for p in kept), key=lambda o: o.index)
    rows = [(o, _values(o.outcome, unit)) for o in effective]
    usable = [values for _, values in rows if values is not None]
    missing = len(effective) - len(usable)
    warnings: list[str] = []
    if not effective:
        warnings.append("no_data")
    elif len(effective) < min_effective:
        warnings.append("small_sample")
    if missing:
        warnings.append("missing_atr")
    base = HorizonForecast(
        horizon, unit, len(ready), len(effective), censored, missing, warnings=warnings
    )
    if not usable:
        return base
    returns = [v[0] for v in usable]
    probabilities = [
        ThresholdProbability(
            k,
            sum(r >= k for r in returns) / len(returns),
            sum(r <= -k for r in returns) / len(returns),
            bootstrap_ci([float(r >= k) for r in returns], seed=seed),
            bootstrap_ci([float(r <= -k) for r in returns], seed=seed),
        )
        for k in thresholds
    ]
    return HorizonForecast(
        horizon,
        unit,
        len(ready),
        len(effective),
        censored,
        missing,
        mean_ret=mean(returns),
        median_ret=median(returns),
        ret_ci=bootstrap_ci(returns, seed=seed),
        quantiles={q: percentile(returns, q) for q in QUANTILES},
        median_mfe=median(v[1] for v in usable),
        median_mae=median(v[2] for v in usable),
        probabilities=probabilities,
        warnings=warnings,
    )
