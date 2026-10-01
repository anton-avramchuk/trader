"""REST прогноза Forecast (ADR-0026): Empirical и KNN рядом, в ценовой системе.

Запрос — как у аналогов (ADR-0025): вхождение `key` или окно последних баров ряда
`query_run_id`; история — вхождения прогонов `run_id`. Empirical строится только для
вхождения (по типу и направлению, опционально по режиму), KNN — по ближайшим
аналогам и для вхождения, и для окна. Методы не смешиваются. Всё считается на лету,
только по данным до `as_of`, результат кэшируется по версиям прогонов и параметрам.
"""

import json
from collections import OrderedDict
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from trader_db import events_statement, to_event
from trader_db.models import EngineRun
from trader_engine.analogues.library import (
    DEFAULT_WINDOW,
    MIN_WINDOW,
    build_candidates,
)
from trader_engine.analogues.pip import DEFAULT_PIP_POINTS, Normalization
from trader_engine.analogues.search import find_analogues
from trader_engine.forecast.calibration import MIN_HISTORY, CalibrationReport, calibrate
from trader_engine.forecast.core import THRESHOLDS
from trader_engine.forecast.methods import (
    MethodForecast,
    empirical_forecast,
    knn_forecast,
)
from trader_engine.stats.outcomes import DEFAULT_HORIZONS

from trader_api.analogues_api import (
    FormationOut,
    _entry_time,  # pyright: ignore[reportPrivateUsage]
    _formation_out,  # pyright: ignore[reportPrivateUsage]
    _last_close,  # pyright: ignore[reportPrivateUsage]
    prepare,
)
from trader_api.deps import DbSession
from trader_api.stats_api import (
    NOT_FOUND,
    IntervalOut,
    _horizons,  # pyright: ignore[reportPrivateUsage]
    _items,  # pyright: ignore[reportPrivateUsage]
    _load,  # pyright: ignore[reportPrivateUsage]
    _runs,  # pyright: ignore[reportPrivateUsage]
)

router = APIRouter(tags=["forecast"])

MAX_THRESHOLDS = 6
CACHE_SIZE = 32

_cache: OrderedDict[str, "ForecastOut"] = OrderedDict()


class Orm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProbabilityOut(Orm):
    threshold: float = Field(description="Порог в единицах прогноза (ATR или %)")
    up: float = Field(description="P(доход ≥ +порог) по цене")
    down: float = Field(description="P(доход ≤ −порог) по цене")
    up_ci: IntervalOut | None
    down_ci: IntervalOut | None


class QuantileOut(Orm):
    q: int
    value: float


class HorizonForecastOut(Orm):
    horizon: int = Field(description="Горизонт, баров торгового времени TF ряда")
    unit: Literal["atr", "pct"]
    n_raw: int
    n_effective: int = Field(description="После de-overlap (окно = горизонт)")
    censored: int
    missing_atr: int
    mean_ret: float | None
    median_ret: float | None
    ret_ci: IntervalOut | None
    quantiles: list[QuantileOut]
    median_mfe: float | None = Field(description="Медианный максимальный рост по цене")
    median_mae: float | None = Field(description="Медианное максимальное падение")
    probabilities: list[ProbabilityOut]
    warnings: list[str]


class MethodOut(Orm):
    method: Literal["empirical", "knn"]
    sample: int = Field(description="Вхождений (empirical) или аналогов (KNN)")
    horizons: list[HorizonForecastOut]
    warnings: list[str]


class ForecastOut(Orm):
    query: FormationOut
    occurrence_key: str | None
    direction: Literal["bullish", "bearish"] | None = Field(
        description="Направление вхождения-запроса; у окна нет"
    )
    as_of: datetime
    unit: Literal["atr", "pct"]
    thresholds: list[float]
    empirical: MethodOut | None = Field(
        description="Только для вхождения-запроса; у окна — null"
    )
    knn: MethodOut
    warnings: list[str]


def _method_out(forecast: MethodForecast) -> MethodOut:
    return MethodOut(
        method=forecast.method,
        sample=forecast.sample,
        horizons=[
            HorizonForecastOut(
                horizon=h.horizon,
                unit=h.unit,
                n_raw=h.n_raw,
                n_effective=h.n_effective,
                censored=h.censored,
                missing_atr=h.missing_atr,
                mean_ret=h.mean_ret,
                median_ret=h.median_ret,
                ret_ci=None
                if h.ret_ci is None
                else IntervalOut.model_validate(h.ret_ci),
                quantiles=[
                    QuantileOut(q=q, value=v) for q, v in sorted(h.quantiles.items())
                ],
                median_mfe=h.median_mfe,
                median_mae=h.median_mae,
                probabilities=[
                    ProbabilityOut.model_validate(p) for p in h.probabilities
                ],
                warnings=h.warnings,
            )
            for h in forecast.horizons
        ],
        warnings=forecast.warnings,
    )


def _cache_key(runs: Sequence[EngineRun], **params: Any) -> str:
    versions = [(r.id, r.updated_at.isoformat(), r.bars_processed) for r in runs]
    return json.dumps([versions, params], sort_keys=True, default=str)


def _remember(key: str, value: ForecastOut) -> None:
    _cache[key] = value
    _cache.move_to_end(key)
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)


def _forecast(
    history: Sequence[EngineRun],
    query_run: EngineRun,
    loaded: Any,
    *,
    key: str | None,
    as_of: datetime,
    window: int,
    pip_points: int,
    normalization: Normalization,
    include_candidates: bool,
    k: int,
    max_distance: float | None,
    band: float,
    horizons: tuple[int, ...],
    unit: Literal["atr", "pct"],
    thresholds: tuple[float, ...],
    same_regime: bool,
    seed: int,
) -> ForecastOut:
    prepared = prepare(
        history,
        query_run,
        loaded,
        key=key,
        as_of=as_of,
        window=window,
        pip_points=pip_points,
        normalization=normalization,
        include_candidates=include_candidates,
    )
    candidates = build_candidates(
        prepared.items,
        as_of=as_of,
        window=window,
        k=pip_points,
        mode=normalization,
        exclude=prepared.query,
    )
    found = find_analogues(
        prepared.query,
        candidates,
        k=k,
        max_distance=max_distance,
        band=band,
        horizons=horizons,
        unit=unit,
        seed=seed,
    )
    knn = knn_forecast(
        found.matches, horizons=horizons, unit=unit, thresholds=thresholds, seed=seed
    )
    empirical: MethodForecast | None = None
    warnings: list[str] = []
    if key is None:
        warnings.append("empirical_needs_occurrence")
    elif prepared.query_item is None:
        warnings.append("occurrence_not_found")
    else:
        empirical = empirical_forecast(
            prepared.query_item,
            prepared.items,
            as_of=as_of,
            horizons=horizons,
            unit=unit,
            thresholds=thresholds,
            same_regime=same_regime,
            seed=seed,
        )
    direction = (
        None
        if prepared.query_item is None
        else prepared.query_item.occurrence.direction
    )
    return ForecastOut(
        query=_formation_out(prepared.query_series, prepared.query),
        occurrence_key=key,
        direction=direction,
        as_of=as_of,
        unit=unit,
        thresholds=list(thresholds),
        empirical=None if empirical is None else _method_out(empirical),
        knn=_method_out(knn),
        warnings=warnings,
    )


@router.get(
    "/forecast",
    response_model=ForecastOut,
    operation_id="getForecast",
    summary="Прогноз исходов: Empirical и KNN",
    description=(
        "Вероятности роста и падения на порогах (в ATR или %), квантили дохода, "
        "медианные MFE/MAE и 95% CI на горизонтах. Два метода рядом, в ценовой "
        "системе (вверх — плюс): **Empirical** — исходы вхождений того же типа и "
        "направления (только для вхождения-запроса `key`), **KNN** — исходы "
        "ближайших по DTW аналогов (и для вхождения, и для окна последних `window` "
        "баров). Только то, что известно на `as_of` (у вхождения по умолчанию — "
        "момент подтверждения). Это статистика прошлого, а не гарантия: смотрите "
        "N, эффективный N и предупреждения. Результат кэшируется."
    ),
    responses=NOT_FOUND | {422: {"description": "Неверные параметры"}},
)
async def forecast(
    session: DbSession,
    query_run_id: Annotated[int, Query(description="Прогон, чей ряд — запрос")],
    run_id: Annotated[
        list[int] | None, Query(description="Прогоны истории (по умолчанию — запроса)")
    ] = None,
    key: Annotated[
        str | None, Query(description="Ключ вхождения-запроса; без него — окно")
    ] = None,
    horizon: Annotated[list[int] | None, Query(description="Горизонты, баров")] = None,
    unit: Annotated[Literal["atr", "pct"], Query()] = "atr",
    threshold: Annotated[
        list[float] | None, Query(description="Пороги в единицах прогноза")
    ] = None,
    same_regime: Annotated[
        bool,
        Query(description="Empirical: только тот же режим (тренд × волатильность)"),
    ] = False,
    window: Annotated[int, Query(ge=MIN_WINDOW, le=500)] = DEFAULT_WINDOW,
    pip_points: Annotated[int, Query(ge=3, le=30)] = DEFAULT_PIP_POINTS,
    normalization: Annotated[Normalization, Query()] = "atr",
    k: Annotated[int, Query(ge=1, le=100)] = 20,
    max_distance: Annotated[float | None, Query(ge=0)] = None,
    band: Annotated[float, Query(ge=0, le=1)] = 0.2,
    include_candidates: Annotated[bool, Query()] = False,
    as_of: Annotated[AwareDatetime | None, Query()] = None,
    seed: Annotated[int, Query()] = 0,
) -> ForecastOut:
    horizons = _horizons(horizon) if horizon else DEFAULT_HORIZONS
    thresholds = tuple(sorted(set(threshold))) if threshold else THRESHOLDS
    if len(thresholds) > MAX_THRESHOLDS or any(not 0 < t <= 50 for t in thresholds):
        raise HTTPException(
            422, f"Пороги: до {MAX_THRESHOLDS} значений, больше 0 и не больше 50"
        )
    history = await _runs(session, run_id or [query_run_id])
    [query_run] = await _runs(session, [query_run_id])
    all_runs = [query_run, *[r for r in history if r.id != query_run.id]]
    effective = as_of
    if effective is None and key is not None:
        rows = await session.run_sync(
            lambda sync: sync.scalars(events_statement(query_run.id)).all()
        )
        effective = _entry_time([to_event(row) for row in rows], query_run.engine, key)
        if effective is None:
            raise HTTPException(
                404, f"Вхождение {key} не найдено в прогоне {query_run.id}"
            )
    cache_key = _cache_key(
        all_runs,
        history=[r.id for r in history],
        query=query_run.id,
        key=key,
        window=window,
        pip_points=pip_points,
        normalization=normalization,
        k=k,
        max_distance=max_distance,
        band=band,
        horizons=horizons,
        unit=unit,
        thresholds=thresholds,
        same_regime=same_regime,
        include_candidates=include_candidates,
        as_of=effective,
        seed=seed,
    )
    cached = _cache.get(cache_key)
    if cached is not None:
        _cache.move_to_end(cache_key)
        return cached
    loaded = await session.run_sync(lambda sync: _load(sync, all_runs, effective))
    resolved = effective or _last_close(loaded, query_run)
    out = await run_in_threadpool(
        _forecast,
        history,
        query_run,
        loaded,
        key=key,
        as_of=resolved,
        window=window,
        pip_points=pip_points,
        normalization=normalization,
        include_candidates=include_candidates,
        k=k,
        max_distance=max_distance,
        band=band,
        horizons=horizons,
        unit=unit,
        thresholds=thresholds,
        same_regime=same_regime,
        seed=seed,
    )
    _remember(cache_key, out)
    return out


class BinOut(Orm):
    low: float
    high: float
    n: int
    mean_predicted: float | None
    observed: float | None
    observed_low: float | None = Field(description="Интервал Уилсона 95%")
    observed_high: float | None


class ThresholdCalibrationOut(Orm):
    threshold: float
    side: Literal["up", "down"] = Field(description="Рост или падение ≥ порога")
    n: int
    brier: float
    brier_climatology: float = Field(description="Brier константы = общей частоте")
    skill: float | None = Field(description="1 − Brier / Brier климатологии")
    bins: list[BinOut]


class CalibrationOut(Orm):
    group: str
    direction: Literal["bullish", "bearish"]
    horizon: int
    unit: Literal["atr", "pct"]
    thresholds: list[float]
    occurrences: int = Field(description="Вхождений выбранного типа и направления")
    tested: int = Field(description="Прогнозов, сверенных с исходом")
    skipped: int
    rows: list[ThresholdCalibrationOut]
    warnings: list[str]


_calibration_cache: OrderedDict[str, CalibrationOut] = OrderedDict()


def _calibration(
    runs: Sequence[EngineRun],
    loaded: Any,
    *,
    group: str,
    direction: str,
    horizon: int,
    unit: Literal["atr", "pct"],
    thresholds: tuple[float, ...],
    min_history: int,
    as_of: datetime | None,
) -> CalibrationOut:
    items = [
        i
        for i in _items(runs, loaded, as_of, include_candidates=False)
        if i.occurrence.group == group and i.occurrence.direction == direction
    ]
    report: CalibrationReport = calibrate(
        items,
        horizon=horizon,
        unit=unit,
        thresholds=thresholds,
        min_history=min_history,
        until=as_of,
    )
    return CalibrationOut(
        group=group,
        direction=direction,  # type: ignore[arg-type]
        horizon=horizon,
        unit=unit,
        thresholds=list(thresholds),
        occurrences=len(items),
        tested=report.tested,
        skipped=report.skipped,
        rows=[ThresholdCalibrationOut.model_validate(r) for r in report.rows],
        warnings=report.warnings,
    )


@router.get(
    "/forecast/calibration",
    response_model=CalibrationOut,
    operation_id="getForecastCalibration",
    summary="Калибровка Empirical-прогноза (walk-forward)",
    description=(
        "Надёжность вероятностей: для каждого вхождения выбранных типа и "
        "направления прогноз строится только по более ранним вхождениям с уже "
        "закрытым исходом и сверяется с реальным исходом. Brier score, reliability "
        "по корзинам (интервал Уилсона) и сравнение с константой — общей частотой. "
        "Исходы в направлении события. `as_of` отсекает и события, и бары."
    ),
    responses=NOT_FOUND | {422: {"description": "Неверные параметры"}},
)
async def forecast_calibration(
    session: DbSession,
    run_id: Annotated[list[int], Query(description="Прогоны истории")],
    group: Annotated[str, Query(description="Тип паттерна")],
    direction: Annotated[Literal["bullish", "bearish"], Query()],
    horizon: Annotated[int, Query(ge=1, le=500)] = 10,
    unit: Annotated[Literal["atr", "pct"], Query()] = "atr",
    threshold: Annotated[list[float] | None, Query()] = None,
    min_history: Annotated[int, Query(ge=5, le=500)] = MIN_HISTORY,
    as_of: Annotated[AwareDatetime | None, Query()] = None,
) -> CalibrationOut:
    thresholds = tuple(sorted(set(threshold))) if threshold else THRESHOLDS
    if len(thresholds) > MAX_THRESHOLDS or any(not 0 < t <= 50 for t in thresholds):
        raise HTTPException(
            422, f"Пороги: до {MAX_THRESHOLDS} значений, больше 0 и не больше 50"
        )
    runs = await _runs(session, run_id)
    cache_key = _cache_key(
        runs,
        group=group,
        direction=direction,
        horizon=horizon,
        unit=unit,
        thresholds=thresholds,
        min_history=min_history,
        as_of=as_of,
    )
    cached = _calibration_cache.get(cache_key)
    if cached is not None:
        _calibration_cache.move_to_end(cache_key)
        return cached
    loaded = await session.run_sync(lambda sync: _load(sync, runs, as_of))
    out = await run_in_threadpool(
        _calibration,
        runs,
        loaded,
        group=group,
        direction=direction,
        horizon=horizon,
        unit=unit,
        thresholds=thresholds,
        min_history=min_history,
        as_of=as_of,
    )
    _calibration_cache[cache_key] = out
    while len(_calibration_cache) > CACHE_SIZE:
        _calibration_cache.popitem(last=False)
    return out
