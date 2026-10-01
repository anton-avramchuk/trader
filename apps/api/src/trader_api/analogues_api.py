"""REST поиска исторических аналогов (ADR-0025).

Запрос — вхождение (`key`) или окно последних баров ряда прогона `query_run_id`;
история — вхождения из логов прогонов `run_id` (область поиска задаёт состав
прогонов: свой инструмент или все того же TF). Ничего не хранится: PIP, DTW и
исходы считаются на каждый запрос, результат кэшируется по версиям прогонов и
параметрам. Берётся только то, что было известно на `as_of`; у запроса-вхождения
по умолчанию это момент его подтверждения.
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
    Formation,
    build_candidates,
    formation_at,
)
from trader_engine.analogues.pip import DEFAULT_PIP_POINTS, Normalization
from trader_engine.analogues.search import AnalogueResult, find_analogues
from trader_engine.stats.occurrences import level_occurrences, pattern_occurrences
from trader_engine.stats.outcomes import DEFAULT_HORIZONS
from trader_engine.stats.pipeline import Series, build_series, collect

from trader_api.deps import DbSession
from trader_api.stats_api import (
    NOT_FOUND,
    HorizonOutcomeOut,
    OccurrenceOut,
    StatsOut,
    _horizons,  # pyright: ignore[reportPrivateUsage]
    _load,  # pyright: ignore[reportPrivateUsage]
    _Loaded,  # pyright: ignore[reportPrivateUsage]
    _runs,  # pyright: ignore[reportPrivateUsage]
    _series_key,  # pyright: ignore[reportPrivateUsage]
)

router = APIRouter(tags=["analogues"])

MAX_WINDOW = 500
MAX_K = 100
CACHE_SIZE = 32

_cache: OrderedDict[str, "AnaloguesOut"] = OrderedDict()


class Orm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ShapeOut(Orm):
    indices: list[int] = Field(description="Индексы PIP-точек в окне (от 0)")
    times: list[float] = Field(description="Время точек в долях ширины, 0…1")
    values: list[float] = Field(description="Цена от первой точки (ATR или %)")


class FormationOut(Orm):
    series_key: str
    start: datetime = Field(description="Начало первого бара окна")
    end: datetime = Field(description="Закрытие последнего бара окна (точка входа)")
    shape: ShapeOut


class MatchOut(Orm):
    occurrence: OccurrenceOut
    formation: FormationOut
    distance: float
    normalized_distance: float = Field(description="Накопленное / длина пути")
    similarity: float = Field(description="1 / (1 + normalized_distance), (0, 1]")
    path: list[list[int]] = Field(description="Выравнивание точек (запрос, аналог)")
    outcomes: list[HorizonOutcomeOut]
    trajectory: list[float] | None = Field(
        description="Сдвиг close после входа по барам, в единицах запроса"
    )


class PercentileOut(Orm):
    q: int
    values: list[float]


class AnaloguesOut(Orm):
    query: FormationOut
    occurrence_key: str | None = Field(description="Ключ вхождения-запроса, если был")
    as_of: datetime = Field(description="Момент, которым ограничены история и бары")
    unit: Literal["atr", "pct"]
    considered: int = Field(description="Кандидатов до отбора")
    matches: list[MatchOut]
    stats: StatsOut = Field(description="Исходы аналогов, статистика MVP-5")
    percentiles: list[PercentileOut] = Field(
        description="Перцентили 25/50/75 траекторий по барам после входа"
    )
    trajectory_count: int
    warnings: list[str]


def _formation_out(series: Series, formation: Formation) -> FormationOut:
    return FormationOut(
        series_key=formation.series_key,
        start=series.bars[formation.start].timestamp,
        end=series.bars[formation.end].close_time,
        shape=ShapeOut(
            indices=list(formation.shape.indices),
            times=list(formation.shape.times),
            values=list(formation.shape.values),
        ),
    )


def _cache_key(runs: Sequence[EngineRun], **params: Any) -> str:
    versions = [(r.id, r.updated_at.isoformat(), r.bars_processed) for r in runs]
    return json.dumps([versions, params], sort_keys=True, default=str)


def _remember(key: str, value: AnaloguesOut) -> None:
    _cache[key] = value
    _cache.move_to_end(key)
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)


def _entry_time(events: Sequence[Any], engine: str, key: str) -> datetime | None:
    """Момент входа вхождения ``key`` по логу движка (кандидаты тоже подходят)."""
    for occurrence in (
        *pattern_occurrences(engine, events, include_candidates=True),
        *level_occurrences(engine, events),
    ):
        if occurrence.key == key:
            return occurrence.available_at
    return None


def _search(
    history: Sequence[EngineRun],
    query_run: EngineRun,
    loaded: _Loaded,
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
    seed: int,
) -> AnaloguesOut:
    series: dict[str, Series] = {}
    for run in (query_run, *history):
        series_key = _series_key(run)
        if series_key not in series:
            series[series_key] = build_series(
                series_key,
                loaded.bars[series_key],
                roll_times=loaded.rolls[series_key],
                as_of=as_of,
            )
    items = []
    for run in history:
        events = [e for e in loaded.events[run.id] if e.available_at <= as_of]
        items += collect(
            series[_series_key(run)],
            [(run.engine, events)],
            include_candidates=include_candidates,
        )
    query_series = series[_series_key(query_run)]
    if key is None:
        end = len(query_series.bars) - 1
    else:
        entry = query_series.index.get(_entry_at(loaded, query_run, key, as_of))
        if entry is None:
            raise HTTPException(422, "У вхождения-запроса нет бара входа в данных")
        end = entry
    unit: Literal["atr", "pct"] = "atr" if normalization == "atr" else "pct"
    query = (
        formation_at(query_series, end, window=window, k=pip_points, mode=normalization)
        if end >= 0
        else None
    )
    if query is None:
        raise HTTPException(
            422, "Для запроса не хватает баров или ATR (окно и прогрев индикатора)"
        )
    candidates = build_candidates(
        items,
        as_of=as_of,
        window=window,
        k=pip_points,
        mode=normalization,
        exclude=query,
    )
    result: AnalogueResult = find_analogues(
        query,
        candidates,
        k=k,
        max_distance=max_distance,
        band=band,
        horizons=horizons,
        unit=unit,
        seed=seed,
    )
    return AnaloguesOut(
        query=_formation_out(query_series, query),
        occurrence_key=key,
        as_of=as_of,
        unit=unit,
        considered=result.considered,
        matches=[
            MatchOut(
                occurrence=OccurrenceOut.model_validate(m.candidate.occurrence),
                formation=_formation_out(m.candidate.series, m.candidate.formation),
                distance=m.dtw.distance,
                normalized_distance=m.dtw.normalized_distance,
                similarity=m.dtw.similarity,
                path=[list(step) for step in m.dtw.path],
                outcomes=[HorizonOutcomeOut.model_validate(o) for o in m.outcomes],
                trajectory=m.trajectory,
            )
            for m in result.matches
        ],
        stats=StatsOut.model_validate(result.stats),
        percentiles=[
            PercentileOut(q=q, values=values)
            for q, values in result.percentiles.items()
        ],
        trajectory_count=result.trajectory_count,
        warnings=result.warnings,
    )


def _entry_at(loaded: _Loaded, run: EngineRun, key: str, as_of: datetime) -> datetime:
    found = _entry_time(loaded.events[run.id], run.engine, key)
    if found is None or found > as_of:
        raise HTTPException(404, f"Вхождение {key} не найдено в прогоне {run.id}")
    return found


@router.get(
    "/analogues",
    response_model=AnaloguesOut,
    operation_id="getAnalogues",
    summary="Исторические аналоги формации",
    description=(
        "Ближайшие по DTW исторические формации к запросу — вхождению паттерна или "
        "события уровня (`key`) либо окну последних `window` баров ряда прогона "
        "`query_run_id`. Формация сжимается до PIP-точек и нормализуется (ATR или "
        "процент); расстояние — DTW с полосой Сакоэ–Чибы. История — вхождения из "
        "прогонов `run_id` (состав прогонов задаёт область поиска), только то, что "
        "известно на `as_of`; окно запроса и пересекающиеся с ним вхождения "
        "исключаются. Ответ: аналоги с исходами, статистика MVP-5 по ним, "
        "траектории и перцентили 25/50/75. Результат кэшируется."
    ),
    responses=NOT_FOUND | {422: {"description": "Неверные параметры"}},
)
async def analogues(
    session: DbSession,
    query_run_id: Annotated[int, Query(description="Прогон, чей ряд — запрос")],
    run_id: Annotated[
        list[int] | None, Query(description="Прогоны истории (по умолчанию — запроса)")
    ] = None,
    key: Annotated[
        str | None, Query(description="Ключ вхождения-запроса; без него — окно")
    ] = None,
    window: Annotated[int, Query(ge=MIN_WINDOW, le=MAX_WINDOW)] = DEFAULT_WINDOW,
    pip_points: Annotated[int, Query(ge=3, le=30)] = DEFAULT_PIP_POINTS,
    normalization: Annotated[Normalization, Query()] = "atr",
    k: Annotated[int, Query(ge=1, le=MAX_K)] = 20,
    max_distance: Annotated[float | None, Query(ge=0)] = None,
    band: Annotated[float, Query(ge=0, le=1)] = 0.2,
    horizon: Annotated[list[int] | None, Query(description="Горизонты, баров")] = None,
    include_candidates: Annotated[bool, Query()] = False,
    as_of: Annotated[AwareDatetime | None, Query()] = None,
    seed: Annotated[int, Query()] = 0,
) -> AnaloguesOut:
    horizons = _horizons(horizon) if horizon else DEFAULT_HORIZONS
    history = await _runs(session, run_id or [query_run_id])
    [query_run] = await _runs(session, [query_run_id])
    all_runs = [query_run, *[r for r in history if r.id != query_run.id]]
    # момент запроса: явный as_of, иначе момент входа вхождения, иначе — всё известное
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
        _search,
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
        seed=seed,
    )
    _remember(cache_key, out)
    return out


def _last_close(loaded: _Loaded, run: EngineRun) -> datetime:
    bars = loaded.bars[_series_key(run)]
    if not bars:
        raise HTTPException(422, "В ряду запроса нет баров")
    return bars[-1].close_time
