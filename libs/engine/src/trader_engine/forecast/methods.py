"""Методы Forecast (ADR-0026): Empirical и KNN. Оба — в ценовой системе (вверх — плюс).

Empirical — исходы вхождений того же типа и направления (по желанию — того же
режима), известных на ``as_of``; исходы считаются в направлении события и затем
переводятся в ценовую систему. KNN — исходы ближайших аналогов MVP-6 (они уже в
сыром направлении). Методы не смешиваются: каждый сообщает свою выборку.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from trader_engine.analogues.search import AnalogueMatch
from trader_engine.forecast.core import (
    HorizonForecast,
    forecast_horizon,
    to_price_frame,
)
from trader_engine.stats.aggregate import MIN_EFFECTIVE, Observation, Unit
from trader_engine.stats.outcomes import DEFAULT_HORIZONS, compute_outcomes
from trader_engine.stats.pipeline import SeriesOccurrence

Method = Literal["empirical", "knn"]


@dataclass(frozen=True, slots=True)
class MethodForecast:
    """Прогноз одного метода: выборка и результат по горизонтам."""

    method: Method
    sample: int
    horizons: list[HorizonForecast]
    warnings: list[str] = field(default_factory=list[str])


def _observation(item: SeriesOccurrence, horizon: int) -> Observation | None:
    entry = item.entry_index
    if entry is None:
        return None
    series, occurrence = item.series, item.occurrence
    outcome = compute_outcomes(
        series.bars,
        entry,
        occurrence.direction,
        series.atrs[entry],
        roll_times=series.roll_times,
        horizons=(horizon,),
    )[0]
    return Observation((series.key, occurrence.direction), entry, outcome)


def empirical_forecast(
    query: SeriesOccurrence,
    history: Sequence[SeriesOccurrence],
    *,
    as_of: datetime,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    unit: Unit = "atr",
    same_regime: bool = False,
    seed: int = 0,
    min_effective: int = MIN_EFFECTIVE,
) -> MethodForecast:
    """Прогноз по вхождениям того же типа и направления, что и у запроса."""
    target = query.occurrence
    warnings: list[str] = []
    regime = query.regime
    if same_regime and regime is None:
        warnings.append("unknown_regime")
        same_regime = False
    sample = [
        item
        for item in history
        if item.occurrence.key != target.key
        and item.occurrence.group == target.group
        and item.occurrence.direction == target.direction
        and item.occurrence.available_at <= as_of
        and (not same_regime or item.regime == regime)
    ]
    result: list[HorizonForecast] = []
    for horizon in horizons:
        observations = [
            o for item in sample if (o := _observation(item, horizon)) is not None
        ]
        forecast = forecast_horizon(
            observations,
            horizon=horizon,
            unit=unit,
            seed=seed,
            min_effective=min_effective,
        )
        result.append(
            to_price_frame(forecast) if target.direction == "bearish" else forecast
        )
    if not sample:
        warnings.append("no_history")
    return MethodForecast("empirical", len(sample), result, warnings)


def knn_forecast(
    matches: Sequence[AnalogueMatch],
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    unit: Unit = "atr",
    seed: int = 0,
    min_effective: int = MIN_EFFECTIVE,
) -> MethodForecast:
    """Прогноз по ближайшим аналогам: равные веса, исходы в сыром направлении."""
    result: list[HorizonForecast] = []
    for horizon in horizons:
        observations: list[Observation] = []
        for match in matches:
            candidate = match.candidate
            series, entry = candidate.series, candidate.entry_index
            outcome = compute_outcomes(
                series.bars,
                entry,
                "bullish",
                series.atrs[entry],
                roll_times=series.roll_times,
                horizons=(horizon,),
            )[0]
            observations.append(Observation((series.key, "raw"), entry, outcome))
        result.append(
            forecast_horizon(
                observations,
                horizon=horizon,
                unit=unit,
                seed=seed,
                min_effective=min_effective,
            )
        )
    warnings = [] if matches else ["no_matches"]
    return MethodForecast("knn", len(matches), result, warnings)
