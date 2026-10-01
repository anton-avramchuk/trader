"""REST confluence-зон: кластеризация активных уровней в контексте графика.

Уровни берутся из прогонов движка `levels` по source TF (не младше chart TF),
известные к `as_of` (без `as_of` — все). Близость меряется в ATR таймфрейма
графика (`atr_timeframe`, для Research задаётся параметром). Зоны считаются на
лету и не хранятся (ADR-0012, spec §21).
"""

import json
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import select
from trader_db import events_statement, to_event
from trader_db.models import EngineRun
from trader_engine.events import create, current_events, hash_params
from trader_engine.events.atr import WilderAtr
from trader_engine.events.zones import (
    Zone,
    ZoneLevel,
    active_levels,
    cluster_levels,
)
from trader_engine.indicators import validate_source_timeframe

from trader_api.candles_api import _fetch
from trader_api.deps import DbSession
from trader_api.indicators_api import _bars

router = APIRouter(tags=["engines"])

ATR_BARS = 600
INVALID: dict[int | str, dict[str, Any]] = {
    404: {"description": "Не найдено"},
    422: {"description": "Некорректные параметры или source TF младше chart TF"},
}


class ZoneMember(BaseModel):
    id: int
    price: float
    source: str
    family: str
    role: str
    strength: float
    source_timeframe: str
    created_at: str = Field(description="Когда уровень появился (ISO)")
    distance_atr: float = Field(description="Расстояние до центра зоны в ATR")


class ZoneOut(BaseModel):
    low: float
    high: float
    center: float
    width_atr: float
    role: str = Field(description="Роль сильнейшего уровня зоны")
    strength: float = Field(
        description="Сила зоны: max силы членов + бонус за семейства"
    )
    families: list[str]
    members: list[ZoneMember]


class ZonesOut(BaseModel):
    chart_timeframe: str
    source_timeframes: list[str]
    atr_timeframe: str
    atr: float | None = Field(description="ATR на момент `as_of`; пусто — нет данных")
    threshold_atr: float
    as_of: AwareDatetime | None
    missing_timeframes: list[str] = Field(
        description="Source TF, по которым нет прогона levels (уровней нет)"
    )
    zones: list[ZoneOut]


def _zone_out(zone: Zone, atr: float) -> ZoneOut:
    return ZoneOut(
        low=zone.low,
        high=zone.high,
        center=zone.center,
        width_atr=zone.width_atr(atr),
        role=zone.role,
        strength=zone.strength,
        families=list(zone.families),
        members=[
            ZoneMember(
                id=m.id,
                price=m.price,
                source=m.source,
                family=m.family,
                role=m.role,
                strength=m.score,
                source_timeframe=m.source_timeframe,
                created_at=m.created_at,
                distance_atr=abs(m.price - zone.center) / atr,
            )
            for m in zone.members
        ],
    )


@router.get(
    "/level-zones",
    response_model=ZonesOut,
    operation_id="getLevelZones",
    summary="Confluence-зоны уровней",
    description=(
        "Активные уровни (не пробитые, не отменённые) прогонов `levels` по "
        "`source_timeframes`, известные к `as_of`, объединяются в зоны: соседние "
        "уровни ближе `threshold_atr` × ATR попадают в одну зону."
    ),
    responses=INVALID,
)
async def get_level_zones(
    session: DbSession,
    chart_timeframe: Annotated[str, Query(description="15m, 1h, 4h, 1d, 1w")],
    instrument_id: int,
    source_timeframes: Annotated[
        list[str] | None, Query(description="По умолчанию — chart TF")
    ] = None,
    atr_timeframe: Annotated[
        str | None, Query(description="TF для ATR; по умолчанию — chart TF")
    ] = None,
    atr_period: Annotated[int, Query(ge=1, le=500)] = 14,
    threshold_atr: Annotated[float, Query(gt=0, le=20)] = 0.5,
    levels_params: Annotated[
        str, Query(description="Параметры движка levels JSON-объектом")
    ] = "{}",
    as_of: AwareDatetime | None = None,
) -> ZonesOut:
    sources = source_timeframes or [chart_timeframe]
    atr_tf = atr_timeframe or chart_timeframe
    try:
        for source in sources:
            validate_source_timeframe(chart_timeframe, source)
        raw: Any = json.loads(levels_params)
        if not isinstance(raw, dict):
            raise ValueError("параметры должны быть JSON-объектом")
        engine = create("levels", raw)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    digest = hash_params(engine.params)

    atr_page = await _fetch(
        session,
        instrument_id=instrument_id,
        timeframe=atr_tf,
        start=None,
        end=None,
        as_of=as_of,
        limit=ATR_BARS,
        tail=True,
    )
    atr_machine = WilderAtr(atr_period)
    for bar in _bars(atr_page.candles):
        atr_machine.update(bar)
    atr = atr_machine.value

    levels: list[ZoneLevel] = []
    missing: list[str] = []
    for source in dict.fromkeys(sources):
        query = (
            select(EngineRun)
            .where(
                EngineRun.engine == "levels",
                EngineRun.params_hash == digest,
                EngineRun.algorithm_version == type(engine).version,
                EngineRun.timeframe_code == source,
                EngineRun.instrument_id == instrument_id,
            )
            .order_by(EngineRun.id.desc())
            .limit(1)
        )
        run = (await session.scalars(query)).one_or_none()
        if run is None:
            missing.append(source)
            continue
        rows = await session.scalars(events_statement(run.id, as_of=as_of))
        events = current_events([to_event(row) for row in rows])
        levels += active_levels(events, source)

    zones = cluster_levels(levels, atr, threshold_atr) if atr else []
    return ZonesOut(
        chart_timeframe=chart_timeframe,
        source_timeframes=list(dict.fromkeys(sources)),
        atr_timeframe=atr_tf,
        atr=atr,
        threshold_atr=threshold_atr,
        as_of=as_of,
        missing_timeframes=missing,
        zones=[_zone_out(zone, atr) for zone in zones] if atr else [],
    )
