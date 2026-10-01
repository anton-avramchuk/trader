"""REST движков событий: каталог, прогоны и лог событий as-of (ADR-0003, ADR-0021).

События неизменяемы; API отдаёт либо полную историю (`view=history`: детекции,
подтверждения, пересмотры, отмены), либо актуальную картину на момент `as_of`
(`view=current`: последняя версия каждой цепочки, отменённые исключены).
Запуск прогона — задача `engine.run` через `POST /jobs`.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import Select, func, select
from trader_db import events_statement, to_event
from trader_db.models import EngineEvent, EngineRun
from trader_engine.events import Event, available, current_events, describe

from trader_api.deps import DbSession

router = APIRouter(tags=["engines"])

MAX_EVENTS = 50000
NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Прогон не найден"}}


class EngineInfo(BaseModel):
    name: str
    title: str
    version: int = Field(description="Версия алгоритма (смена — новый прогон)")
    params_schema: dict[str, Any] = Field(description="JSON-схема параметров")
    defaults: dict[str, Any]


class EngineRunOut(BaseModel):
    id: int
    engine: str
    algorithm_version: int
    params: dict[str, Any]
    params_hash: str
    instrument_id: int
    timeframe: str
    bars_processed: int
    last_close_time: datetime | None
    event_count: int
    created_at: datetime
    updated_at: datetime


class EngineEventOut(BaseModel):
    seq: int
    kind: str
    status: Literal["detected", "confirmed", "revised", "invalidated"]
    payload: dict[str, Any]
    detected_at: datetime
    confirmed_at: datetime | None
    available_at: datetime = Field(description="С какого момента событие известно")
    revises: int | None = Field(description="seq пересматриваемого события")


def _run_query() -> Select[EngineRun, int]:
    counts = (
        select(EngineEvent.run_id, func.count().label("events"))
        .group_by(EngineEvent.run_id)
        .subquery()
    )
    return select(EngineRun, func.coalesce(counts.c.events, 0)).outerjoin(
        counts, counts.c.run_id == EngineRun.id
    )


def _run_out(run: EngineRun, event_count: int) -> EngineRunOut:
    return EngineRunOut(
        id=run.id,
        engine=run.engine,
        algorithm_version=run.algorithm_version,
        params=run.params,
        params_hash=run.params_hash,
        instrument_id=run.instrument_id,
        timeframe=run.timeframe_code,
        bars_processed=run.bars_processed,
        last_close_time=run.last_close_time,
        event_count=event_count,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _event_out(event: Event) -> EngineEventOut:
    return EngineEventOut(
        seq=event.seq,
        kind=event.kind,
        status=event.status,
        payload=event.payload,
        detected_at=event.detected_at,
        confirmed_at=event.confirmed_at,
        available_at=event.available_at,
        revises=event.revises,
    )


@router.get(
    "/engines",
    response_model=list[EngineInfo],
    operation_id="listEngines",
    summary="Каталог движков событий",
    description="Зарегистрированные движки с JSON-схемой параметров и умолчаниями.",
)
async def list_engines() -> list[EngineInfo]:
    return [EngineInfo.model_validate(describe(cls)) for cls in available()]


@router.get(
    "/engine-runs",
    response_model=list[EngineRunOut],
    operation_id="listEngineRuns",
    summary="Прогоны движков",
    description="Новые сверху. Фильтры по движку, инструменту и таймфрейму.",
)
async def list_runs(
    session: DbSession,
    engine: Annotated[str | None, Query(description="Имя движка")] = None,
    instrument_id: Annotated[int | None, Query()] = None,
    timeframe: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[EngineRunOut]:
    query = _run_query()
    if engine is not None:
        query = query.where(EngineRun.engine == engine)
    if instrument_id is not None:
        query = query.where(EngineRun.instrument_id == instrument_id)
    if timeframe is not None:
        query = query.where(EngineRun.timeframe_code == timeframe)
    rows = await session.execute(query.order_by(EngineRun.id.desc()).limit(limit))
    return [_run_out(run, count) for run, count in rows.all()]


async def _find_run(session: DbSession, run_id: int) -> EngineRunOut:
    row = (
        await session.execute(_run_query().where(EngineRun.id == run_id))
    ).one_or_none()
    if row is None:
        raise HTTPException(404, f"Прогон {run_id} не найден")
    return _run_out(row[0], row[1])


@router.get(
    "/engine-runs/{run_id}",
    response_model=EngineRunOut,
    operation_id="getEngineRun",
    summary="Прогон движка",
    responses=NOT_FOUND,
)
async def get_run(run_id: int, session: DbSession) -> EngineRunOut:
    return await _find_run(session, run_id)


@router.get(
    "/engine-runs/{run_id}/events",
    response_model=list[EngineEventOut],
    operation_id="listEngineEvents",
    summary="События прогона (в том числе as-of)",
    description=(
        "`as_of` — срез знания: только события с `available_at <= as_of`. "
        "`view=history` — все события по порядку (включая пересмотры и отмены), "
        "`view=current` — актуальная картина на `as_of`: последняя версия каждой "
        "цепочки, отменённые исключены. `after_seq` — только события после этого "
        "номера (для догрузки)."
    ),
    responses=NOT_FOUND
    | {422: {"description": "Слишком много событий: сузьте выборку"}},
)
async def list_events(
    run_id: int,
    session: DbSession,
    as_of: Annotated[AwareDatetime | None, Query()] = None,
    kind: Annotated[list[str] | None, Query(description="Типы событий")] = None,
    view: Annotated[Literal["history", "current"], Query()] = "history",
    after_seq: Annotated[int | None, Query(ge=0)] = None,
) -> list[EngineEventOut]:
    await _find_run(session, run_id)
    # Цепочки ревизий разрешаются на всём срезе, поэтому after_seq — после разрешения.
    query = events_statement(run_id, as_of=as_of, kinds=kind)
    if view == "history" and after_seq is not None:
        query = events_statement(run_id, as_of=as_of, kinds=kind, after_seq=after_seq)
    rows = (await session.scalars(query.limit(MAX_EVENTS + 1))).all()
    if len(rows) > MAX_EVENTS:
        raise HTTPException(422, f"Больше {MAX_EVENTS} событий: сузьте выборку")
    events = [to_event(row) for row in rows]
    if view == "current":
        events = current_events(events, as_of)
        if after_seq is not None:
            events = [event for event in events if event.seq > after_seq]
    return [_event_out(event) for event in events]
