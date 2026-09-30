"""REST исторической статистики: исходы вхождений паттернов и уровней (ADR-0023).

Статистика не хранится: считается из логов событий (`engine_events`) и свечей ряда
на каждый запрос, результат кэшируется по прогонам (их версии и числу обработанных
баров), параметрам и фильтрам. `as_of` отсекает и события, и бары: так выборка
совпадает с тем, что знал бы наблюдатель в этот момент (replay).
"""

import json
from collections import OrderedDict
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from trader_db import events_statement, read_bars, read_continuous, to_event
from trader_db.continuous import load_rolls
from trader_db.models import EngineRun
from trader_engine.events import Event
from trader_engine.indicators import BarInput
from trader_engine.stats.outcomes import DEFAULT_HORIZONS
from trader_engine.stats.pipeline import (
    Filters,
    Series,
    SeriesOccurrence,
    StatsResult,
    build_series,
    collect,
    compute_statistics,
    occurrence_detail,
)

from trader_api.deps import DbSession

router = APIRouter(tags=["stats"])

MAX_HORIZON = 500
MAX_HORIZONS = 8
MAX_RUNS = 12
CACHE_SIZE = 64
NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Не найдено"}}

_cache: OrderedDict[str, "StatsOut"] = OrderedDict()


class Orm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DistOut(Orm):
    mean: float
    median: float


class IntervalOut(Orm):
    low: float
    high: float


class HorizonStatsOut(Orm):
    horizon: int = Field(description="Горизонт, баров торгового времени TF ряда")
    unit: Literal["atr", "pct"]
    n_raw: int = Field(description="Вхождения с известным исходом до прореживания")
    n_effective: int = Field(description="После de-overlap (окно = горизонт)")
    censored: int = Field(description="Горизонт выходит за данные — не считаются")
    missing_atr: int = Field(description="Без ATR на входе (исключены из ATR-оценок)")
    win_rate: float | None
    ret: DistOut | None
    mfe: DistOut | None
    mae: DistOut | None
    ret_ci: IntervalOut | None = Field(description="95% CI среднего, block bootstrap")
    target_rate: float | None
    invalidated_rate: float | None
    baseline_n: int
    baseline_ret: DistOut | None
    edge: float | None = Field(description="Среднее события минус среднее baseline")
    edge_ci: IntervalOut | None
    flags: dict[str, int]
    warnings: list[str]


class BucketOut(Orm):
    key: str
    n_occurrences: int
    horizons: list[HorizonStatsOut]


class StatsOut(Orm):
    matched: int = Field(description="Вхождений после фильтров")
    unit: Literal["atr", "pct"]
    buckets: list[BucketOut]
    warnings: list[str]
    skipped: int = Field(
        default=0, description="Вхождений без бара входа в данных ряда"
    )


class OccurrenceOut(Orm):
    key: str
    engine: str
    kind: Literal["pattern", "level"]
    group: str
    direction: Literal["bullish", "bearish"]
    entry: Literal["confirmed", "candidate", "touch", "break"]
    available_at: datetime
    target: float | None
    invalidated_at: datetime | None
    final_state: str | None
    reason: str | None
    false_breakout: bool
    quality: float | None
    meta: dict[str, Any]


class HorizonOutcomeOut(Orm):
    horizon: int
    censored: bool
    ret_atr: float | None
    ret_pct: float | None
    mfe_atr: float | None
    mfe_pct: float | None
    mae_atr: float | None
    mae_pct: float | None
    first_hit: Literal["target", "invalidated"] | None
    ambiguous_bar: bool
    crosses_session_gap: bool
    crosses_weekend: bool
    crosses_roll: bool


class OccurrenceDetailOut(Orm):
    occurrence: OccurrenceOut
    regime: str = Field(description="Режим на входе: тренд/волатильность или unknown")
    outcomes: list[HorizonOutcomeOut]


def _horizons(values: list[int] | None) -> tuple[int, ...]:
    chosen = tuple(sorted(set(values))) if values else DEFAULT_HORIZONS
    if len(chosen) > MAX_HORIZONS or any(not 1 <= h <= MAX_HORIZON for h in chosen):
        raise HTTPException(
            422, f"Горизонты: до {MAX_HORIZONS} значений от 1 до {MAX_HORIZON}"
        )
    return chosen


def _series_key(run: EngineRun) -> str:
    scope = f"root:{run.root_id}" if run.root_id else f"contract:{run.contract_id}"
    return f"{scope}:{run.timeframe_code}"


async def _runs(session: DbSession, run_ids: list[int]) -> list[EngineRun]:
    if not run_ids or len(set(run_ids)) > MAX_RUNS:
        raise HTTPException(422, f"Укажите от 1 до {MAX_RUNS} прогонов")
    rows = (
        await session.scalars(select(EngineRun).where(EngineRun.id.in_(run_ids)))
    ).all()
    found = {run.id for run in rows}
    missing = [i for i in dict.fromkeys(run_ids) if i not in found]
    if missing:
        raise HTTPException(404, f"Прогон {missing[0]} не найден")
    return sorted(rows, key=lambda run: run_ids.index(run.id))


class _Loaded:
    """Данные, прочитанные из БД: бары и роллы рядов и события прогонов."""

    def __init__(self) -> None:
        self.bars: dict[str, list[BarInput]] = {}
        self.rolls: dict[str, list[datetime]] = {}
        self.events: dict[int, list[Event]] = {}


def _load(sync: Session, runs: Sequence[EngineRun], as_of: datetime | None) -> _Loaded:
    loaded = _Loaded()
    for run in runs:
        key = _series_key(run)
        if key not in loaded.bars:
            if run.root_id is not None:
                loaded.bars[key] = [
                    BarInput.from_bar(item.bar)
                    for item in read_continuous(
                        sync, run.root_id, run.timeframe_code, as_of=as_of
                    )
                ]
                loaded.rolls[key] = [
                    roll.rolled_at for roll in load_rolls(sync, run.root_id, as_of)
                ]
            else:
                assert run.contract_id is not None
                loaded.bars[key] = [
                    BarInput.from_bar(bar)
                    for bar in read_bars(
                        sync, run.contract_id, run.timeframe_code, closed_until=as_of
                    )
                ]
                loaded.rolls[key] = []
        rows = sync.scalars(events_statement(run.id, as_of=as_of)).all()
        loaded.events[run.id] = [to_event(row) for row in rows]
    return loaded


def _items(
    runs: Sequence[EngineRun],
    loaded: _Loaded,
    as_of: datetime | None,
    include_candidates: bool,
) -> list[SeriesOccurrence]:
    series: dict[str, Series] = {}
    items: list[SeriesOccurrence] = []
    for run in runs:
        key = _series_key(run)
        if key not in series:
            series[key] = build_series(
                key, loaded.bars[key], roll_times=loaded.rolls[key], as_of=as_of
            )
        items += collect(
            series[key],
            [(run.engine, loaded.events[run.id])],
            include_candidates=include_candidates,
        )
    return items


def _cache_key(runs: Sequence[EngineRun], **params: Any) -> str:
    versions = [(r.id, r.updated_at.isoformat(), r.bars_processed) for r in runs]
    return json.dumps([versions, params], sort_keys=True, default=str)


def _remember(key: str, value: StatsOut) -> None:
    _cache[key] = value
    _cache.move_to_end(key)
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)


def _statistics(
    runs: Sequence[EngineRun],
    loaded: _Loaded,
    *,
    as_of: datetime | None,
    include_candidates: bool,
    filters: Filters,
    horizons: tuple[int, ...],
    unit: Literal["atr", "pct"],
    group_by: str,
    baseline: bool,
    per_event: int,
    seed: int,
) -> StatsOut:
    items = _items(runs, loaded, as_of, include_candidates)
    result: StatsResult = compute_statistics(
        items,
        filters=filters,
        horizons=horizons,
        unit=unit,
        group_by=group_by,
        baseline=baseline,
        per_event=per_event,
        seed=seed,
    )
    skipped = sum(1 for item in items if item.entry_index is None)
    out = StatsOut.model_validate(result)
    out.skipped = skipped
    if skipped:
        out.warnings.append("entries_without_bar")
    return out


@router.get(
    "/stats/outcomes",
    response_model=StatsOut,
    operation_id="getOutcomeStats",
    summary="Статистика исходов вхождений",
    description=(
        "Forward outcomes (доходность, MFE, MAE) вхождений паттернов и событий "
        "уровней из логов указанных прогонов. Вход — close бара `available_at` "
        "события; горизонты в барах торгового времени. Выборка прореживается "
        "окном = горизонту (эффективный N), CI — block bootstrap, baseline — "
        "случайные точки того же режима (тренд × волатильность). `as_of` отсекает "
        "события и бары. Результат кэшируется."
    ),
    responses=NOT_FOUND | {422: {"description": "Неверные параметры"}},
)
async def outcome_stats(
    session: DbSession,
    run_id: Annotated[list[int], Query(description="Прогоны движков")],
    horizon: Annotated[list[int] | None, Query(description="Горизонты, баров")] = None,
    unit: Annotated[Literal["atr", "pct"], Query()] = "atr",
    group_by: Annotated[
        Literal["none", "group", "direction", "regime"], Query()
    ] = "none",
    group: Annotated[
        list[str] | None, Query(description="Тип паттерна/события")
    ] = None,
    direction: Annotated[Literal["bullish", "bearish"] | None, Query()] = None,
    final_state: Annotated[
        list[str] | None,
        Query(description="confirmed, candidate, invalidated, touch, break"),
    ] = None,
    include_candidates: Annotated[bool, Query()] = False,
    false_breakout: Annotated[bool | None, Query()] = None,
    quality_min: Annotated[float | None, Query(ge=0, le=100)] = None,
    quality_max: Annotated[float | None, Query(ge=0, le=100)] = None,
    trend: Annotated[list[str] | None, Query()] = None,
    volatility: Annotated[list[str] | None, Query()] = None,
    since: Annotated[AwareDatetime | None, Query()] = None,
    until: Annotated[AwareDatetime | None, Query()] = None,
    as_of: Annotated[AwareDatetime | None, Query()] = None,
    baseline: Annotated[bool, Query()] = True,
    per_event: Annotated[int, Query(ge=1, le=50)] = 5,
    seed: Annotated[int, Query()] = 0,
) -> StatsOut:
    runs = await _runs(session, run_id)
    horizons = _horizons(horizon)
    filters = Filters(
        groups=tuple(group or ()),
        direction=direction,
        final_states=tuple(final_state or ()),
        false_breakout=false_breakout,
        quality_min=quality_min,
        quality_max=quality_max,
        trends=tuple(trend or ()),
        volatilities=tuple(volatility or ()),
        since=since,
        until=until,
    )
    key = _cache_key(
        runs,
        horizons=horizons,
        unit=unit,
        group_by=group_by,
        filters=repr(filters),
        include_candidates=include_candidates,
        as_of=as_of,
        baseline=baseline,
        per_event=per_event,
        seed=seed,
    )
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)
        return cached
    loaded = await session.run_sync(lambda sync: _load(sync, runs, as_of))
    out = await run_in_threadpool(
        _statistics,
        runs,
        loaded,
        as_of=as_of,
        include_candidates=include_candidates,
        filters=filters,
        horizons=horizons,
        unit=unit,
        group_by=group_by,
        baseline=baseline,
        per_event=per_event,
        seed=seed,
    )
    _remember(key, out)
    return out


def _detail(
    run: EngineRun,
    loaded: _Loaded,
    occurrence_key: str,
    as_of: datetime | None,
    horizons: tuple[int, ...],
) -> OccurrenceDetailOut | None:
    for item in _items([run], loaded, as_of, include_candidates=True):
        if item.occurrence.key == occurrence_key:
            detail = occurrence_detail(item, horizons)
            return (
                None if detail is None else OccurrenceDetailOut.model_validate(detail)
            )
    return None


@router.get(
    "/stats/occurrence",
    response_model=OccurrenceDetailOut,
    operation_id="getOccurrenceOutcomes",
    summary="Исходы одного вхождения",
    description=(
        "Исходы вхождения по горизонтам: доходность, MFE, MAE в ATR и процентах, "
        "что раньше — цель или отмена, флаги пересечений и режим на входе. "
        "`key` — ключ вхождения из лога прогона: `движок:id` у паттерна, "
        "`движок:id уровня:seq` у касания и пробоя."
    ),
    responses=NOT_FOUND | {422: {"description": "Неверные параметры"}},
)
async def occurrence_outcomes(
    session: DbSession,
    run_id: Annotated[int, Query()],
    key: Annotated[str, Query(description="Ключ вхождения")],
    horizon: Annotated[list[int] | None, Query(description="Горизонты, баров")] = None,
    as_of: Annotated[AwareDatetime | None, Query()] = None,
) -> OccurrenceDetailOut:
    [run] = await _runs(session, [run_id])
    horizons = _horizons(horizon)
    loaded = await session.run_sync(lambda sync: _load(sync, [run], as_of))
    detail = await run_in_threadpool(_detail, run, loaded, key, as_of, horizons)
    if detail is None:
        raise HTTPException(404, f"Вхождение {key} не найдено в прогоне {run_id}")
    return detail
