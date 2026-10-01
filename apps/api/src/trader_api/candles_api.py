"""REST свечей инструмента: диапазоны и snapshot «как известно на момент t» (ADR-0028).

Свечи приходят готовыми из importer и хранятся как есть: закрытые, без склеек и
поправок. ``close_time`` — момент, с которого свеча известна; срез ``as_of``
оставляет только закрытые к этому моменту свечи.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from trader_db import read_bars
from trader_db.models import Instrument
from trader_engine.timeframes import TIMEFRAMES

from trader_api.deps import DbSession

router = APIRouter(tags=["candles"])

DEFAULT_LIMIT = 5000
MAX_LIMIT = 20000
NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Не найдено"}}
BAD_TIMEFRAME: dict[int | str, dict[str, Any]] = {
    422: {"description": "Неизвестный таймфрейм"}
}


class CandleOut(BaseModel):
    timestamp: datetime = Field(description="Открытие свечи (UTC)")
    close_time: datetime = Field(
        description="Конец свечи; данные доступны с этого момента"
    )
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trading_day: date = Field(description="Дата открытия в часовом поясе инструмента")


class CandlesOut(BaseModel):
    timeframe: str
    instrument_id: int
    as_of: datetime | None = Field(description="Срез знания (snapshot)")
    count: int
    truncated: bool = Field(description="Есть ещё свечи за пределами `limit`")
    next_start: datetime | None = Field(
        description="Откуда продолжить (при выборке с начала диапазона)"
    )
    candles: list[CandleOut]


async def _fetch(
    session: AsyncSession,
    *,
    instrument_id: int,
    timeframe: str,
    start: datetime | None,
    end: datetime | None,
    as_of: datetime | None,
    limit: int,
    tail: bool,
) -> CandlesOut:
    if timeframe not in TIMEFRAMES:
        raise HTTPException(
            422,
            f"Неизвестный таймфрейм {timeframe!r}; доступны: {', '.join(TIMEFRAMES)}",
        )
    if await session.get(Instrument, instrument_id) is None:
        raise HTTPException(404, "Инструмент не найден")
    # на одну свечу больше лимита — так видно, есть ли продолжение
    bars = await session.run_sync(
        lambda sync: read_bars(
            sync,
            instrument_id,
            timeframe,
            start,
            end,
            closed_until=as_of,
            limit=limit + 1,
            tail=tail,
        )
    )
    truncated = len(bars) > limit
    if truncated:
        bars = bars[1:] if tail else bars[:limit]
    next_start = bars[-1].close_time if bars and not tail and truncated else None
    return CandlesOut(
        timeframe=timeframe,
        instrument_id=instrument_id,
        as_of=as_of,
        count=len(bars),
        truncated=truncated,
        next_start=next_start,
        candles=[
            CandleOut(
                timestamp=b.timestamp,
                close_time=b.close_time,
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
                trading_day=b.trading_day,
            )
            for b in bars
        ],
    )


@router.get(
    "/candles",
    response_model=CandlesOut,
    operation_id="getCandles",
    summary="Свечи инструмента",
    description=(
        "Закрытые свечи инструмента на таймфрейме по возрастанию времени, не более "
        "`limit`; при `truncated` продолжайте с `next_start`. С `tail=true` — "
        "последние `limit` свечей диапазона (при `truncated` слева есть более ранние)."
    ),
    responses={**NOT_FOUND, **BAD_TIMEFRAME},
)
async def get_candles(
    session: DbSession,
    instrument_id: int,
    timeframe: Annotated[str, Query(description="15m, 1h, 4h, 1d, 1w")],
    start: Annotated[AwareDatetime | None, Query(description="Включительно")] = None,
    end: Annotated[AwareDatetime | None, Query(description="Исключительно")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    tail: Annotated[
        bool,
        Query(description="Последние `limit` свечей диапазона (подгрузка истории)"),
    ] = False,
) -> CandlesOut:
    return await _fetch(
        session,
        instrument_id=instrument_id,
        timeframe=timeframe,
        start=start,
        end=end,
        as_of=None,
        limit=limit,
        tail=tail,
    )


@router.get(
    "/snapshot",
    response_model=CandlesOut,
    operation_id="getSnapshot",
    summary="Свечи, известные на момент as_of",
    description=(
        "Срез знания: только свечи, закрытые к `as_of` (`close_time <= as_of`). "
        "Возвращает **последние** `limit` свечей (по возрастанию). Основа для "
        "воспроизводимых расчётов без заглядывания в будущее."
    ),
    responses={**NOT_FOUND, **BAD_TIMEFRAME},
)
async def get_snapshot(
    session: DbSession,
    instrument_id: int,
    timeframe: Annotated[str, Query(description="15m, 1h, 4h, 1d, 1w")],
    as_of: Annotated[
        AwareDatetime, Query(description="Момент знания (UTC или со смещением)")
    ],
    start: Annotated[AwareDatetime | None, Query(description="Включительно")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> CandlesOut:
    return await _fetch(
        session,
        instrument_id=instrument_id,
        timeframe=timeframe,
        start=start,
        end=None,
        as_of=as_of,
        limit=limit,
        tail=True,
    )
