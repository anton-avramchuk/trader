"""REST индикаторов: каталог плагинов и значения на баре графика (ADR-0009, ADR-0020).

Индикатор считается по барам **source TF** и проецируется на бары **chart TF**
ступенькой: значение источника доступно с закрытия его бара (`available_at`),
формирующийся старший бар не используется. Младший TF на старшем графике
запрещён (422).
"""

import json
from datetime import datetime
from threading import Lock
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import AwareDatetime, BaseModel, Field
from trader_engine.indicators import (
    BarInput,
    IndicatorCache,
    available,
    create,
    describe,
    get,
    params_hash,
    project,
    validate_source_timeframe,
)

from trader_api.candles_api import MAX_LIMIT, CandleOut, _fetch
from trader_api.deps import DbSession

router = APIRouter(tags=["indicators"])

# Сколько баров source TF берётся для расчёта (глубже — прогрев рекурсивных индикаторов
# начинался бы не с начала истории; тогда `source_truncated = true`).
MAX_SOURCE_BARS = 20000
DEFAULT_CHART_LIMIT = 2000
NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Не найдено"}}
INVALID: dict[int | str, dict[str, Any]] = {
    422: {
        "description": (
            "Неверные параметры индикатора, выбор root/контракта или таймфреймы "
            "(source TF младше chart TF запрещён)"
        )
    }
}


class IndicatorInfo(BaseModel):
    name: str
    title: str
    version: int
    pane: str = Field(
        description="`price` — поверх цены, `separate` — отдельная панель"
    )
    outputs: list[str]
    warmup_bars: int = Field(
        description="Баров source TF до первого валидного значения"
    )
    params_schema: dict[str, Any] = Field(
        description="JSON-схема параметров (для формы)"
    )
    defaults: dict[str, Any]


class IndicatorPoint(BaseModel):
    timestamp: datetime = Field(description="Open time бара графика")
    values: dict[str, float | None]
    valid: bool = Field(description="Прогрев source-серии завершён")
    source_timestamp: datetime | None = Field(
        description="Open time source-бара, давшего значение"
    )
    available_at: datetime | None = Field(
        description="Когда значение стало известно (закрытие source-бара)"
    )


class IndicatorValuesOut(BaseModel):
    indicator: str
    version: int
    params: dict[str, Any]
    params_hash: str
    pane: str
    outputs: list[str]
    chart_timeframe: str
    source_timeframe: str
    warmup_bars: int
    source_bar_count: int = Field(description="Баров source TF использовано в расчёте")
    source_truncated: bool = Field(
        description="История source TF длиннее лимита: прогрев начат не с начала данных"
    )
    as_of: datetime | None
    points: list[IndicatorPoint]


@router.get(
    "/indicators",
    response_model=list[IndicatorInfo],
    operation_id="listIndicators",
    summary="Каталог индикаторов",
    description="Плагины с JSON-схемой параметров и значениями по умолчанию.",
)
async def list_indicators() -> list[IndicatorInfo]:
    return [IndicatorInfo.model_validate(describe(cls)) for cls in available()]


def _bars(candles: list[CandleOut]) -> list[BarInput]:
    return [
        BarInput(
            timestamp=c.timestamp,
            close_time=c.close_time,
            open=float(c.open),
            high=float(c.high),
            low=float(c.low),
            close=float(c.close),
            volume=float(c.volume),
            trading_day=c.trading_day or c.timestamp.date(),
        )
        for c in candles
    ]


@router.get(
    "/indicator-values",
    response_model=IndicatorValuesOut,
    operation_id="getIndicatorValues",
    summary="Значения индикатора на баре графика",
    description=(
        "Считает индикатор по барам `source_timeframe` (по умолчанию как у графика) и "
        "проецирует на последние `limit` баров `chart_timeframe` ступенькой по "
        "`available_at` (без формирующегося старшего бара). С `as_of` — только "
        "закрытые к моменту бары и роллы, известные к нему (масштаб continuous как "
        "тогда). Значения до конца прогрева — `valid = false`."
    ),
    responses={**NOT_FOUND, **INVALID},
)
async def get_indicator_values(
    request: Request,
    session: DbSession,
    indicator: Annotated[str, Query(description="Имя из `GET /indicators`")],
    chart_timeframe: Annotated[str, Query(description="15m, 1h, 4h, 1d, 1w")],
    root_id: int | None = None,
    contract_id: int | None = None,
    source_timeframe: Annotated[
        str | None, Query(description="По умолчанию равен chart_timeframe")
    ] = None,
    params: Annotated[
        str, Query(description="Параметры индикатора JSON-объектом")
    ] = "{}",
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    as_of: AwareDatetime | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_CHART_LIMIT,
) -> IndicatorValuesOut:
    source_tf = source_timeframe or chart_timeframe
    try:
        validate_source_timeframe(chart_timeframe, source_tf)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    try:
        raw_params: Any = json.loads(params)
        if not isinstance(raw_params, dict):
            raise ValueError("параметры должны быть JSON-объектом")
        machine = create(indicator, raw_params)
    except KeyError as error:
        raise HTTPException(404, str(error.args[0])) from error
    except ValueError as error:  # includes json.JSONDecodeError
        raise HTTPException(422, str(error)) from error

    chart_page = await _fetch(
        session,
        root_id=root_id,
        contract_id=contract_id,
        timeframe=chart_timeframe,
        start=start,
        end=end,
        as_of=as_of,
        limit=limit,
        tail=True,
    )
    chart_bars = _bars(chart_page.candles)
    info_cls = get(indicator)
    if not chart_bars:
        return IndicatorValuesOut(
            indicator=indicator,
            version=info_cls.version,
            params=machine.params.model_dump(mode="json"),
            params_hash=params_hash(machine),
            pane=info_cls.pane,
            outputs=list(info_cls.outputs),
            chart_timeframe=chart_timeframe,
            source_timeframe=source_tf,
            warmup_bars=machine.warmup_bars,
            source_bar_count=0,
            source_truncated=False,
            as_of=as_of,
            points=[],
        )

    # Для прогрева нужна вся предшествующая история source TF, а не окно графика.
    history = await _fetch(
        session,
        root_id=root_id,
        contract_id=contract_id,
        timeframe=source_tf,
        start=None,
        end=chart_bars[-1].close_time if as_of is None else None,
        as_of=as_of,
        limit=MAX_SOURCE_BARS,
        tail=True,
    )
    source_bars = _bars(history.candles)
    source_truncated = history.truncated

    cache: IndicatorCache = request.app.state.indicator_cache
    lock: Lock = request.app.state.indicator_lock
    dataset_key = (
        f"{'root' if root_id is not None else 'contract'}:{root_id or contract_id}"
    )

    def compute() -> list[Any]:
        with lock:
            series = cache.compute(dataset_key, source_tf, machine, source_bars)
        return project(chart_bars, source_bars, series)

    projections = await run_in_threadpool(compute)
    points = [
        IndicatorPoint(
            timestamp=bar.timestamp,
            values=projection.values,
            valid=projection.valid,
            source_timestamp=projection.source_timestamp,
            available_at=projection.available_at,
        )
        for bar, projection in zip(chart_bars, projections, strict=True)
    ]
    return IndicatorValuesOut(
        indicator=indicator,
        version=info_cls.version,
        params=machine.params.model_dump(mode="json"),
        params_hash=params_hash(machine),
        pane=info_cls.pane,
        outputs=list(info_cls.outputs),
        chart_timeframe=chart_timeframe,
        source_timeframe=source_tf,
        warmup_bars=machine.warmup_bars,
        source_bar_count=len(source_bars),
        source_truncated=source_truncated,
        as_of=as_of,
        points=points,
    )
