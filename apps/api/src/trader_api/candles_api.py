"""REST свечей: диапазоны, continuous-серия root и snapshot «как известно на момент t».

Время бара — open time, ``close_time`` — конец; данные доступны с ``close_time``
(ADR-0004). Цены и объёмы — строки (Decimal без потери точности). Continuous
(``root_id``) — цены приведены к масштабу текущего контракта (ADR-0019).
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from trader_db import (
    latest_dataset_version,
    load_rolls,
    read_bars,
    read_candles,
    read_continuous,
)
from trader_db.models import Contract, DatasetVersion, RollEvent, Root
from trader_engine.aggregation import TIMEFRAMES

from trader_api.deps import DbSession

router = APIRouter(tags=["candles"])

MINUTE = timedelta(minutes=1)
DEFAULT_LIMIT = 5000
MAX_LIMIT = 20000
NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Не найдено"}}
BAD_SELECTION: dict[int | str, dict[str, Any]] = {
    422: {"description": "Нужен ровно один из root_id / contract_id; неверный TF"}
}


class CandleOut(BaseModel):
    timestamp: datetime = Field(description="Open time бара (UTC)")
    close_time: datetime = Field(
        description="Конец бара; данные доступны с этого момента"
    )
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_count: int | None = None
    is_partial: bool = Field(
        description="Бар короче номинала (сессия, клиринг, граница)"
    )
    trading_day: date | None = Field(
        default=None, description="Торговый день (только TF)"
    )
    contract_id: int | None = Field(
        default=None, description="Контракт бара (только continuous)"
    )
    price_factor: Decimal | None = Field(
        default=None,
        description="Множитель цен к масштабу текущего контракта (только continuous)",
    )


class RollOut(BaseModel):
    from_contract_id: int
    to_contract_id: int
    rolled_at: datetime
    ratio: Decimal
    available_at: datetime


class CandlesOut(BaseModel):
    timeframe: str
    root_id: int | None
    contract_id: int | None
    dataset_version_id: int | None = Field(
        description="Версия датасета 1m (для сырых минут); у баров TF — null"
    )
    as_of: datetime | None = Field(description="Срез знания (snapshot)")
    count: int
    truncated: bool = Field(description="Есть ещё бары за пределами `limit`")
    next_start: datetime | None = Field(
        description="Откуда продолжить (при выборке с начала диапазона)"
    )
    candles: list[CandleOut]
    rolls: list[RollOut] = Field(
        description="Роллы continuous-серии в диапазоне выданных баров"
    )


def _roll_out(event: RollEvent) -> RollOut:
    return RollOut(
        from_contract_id=event.from_contract_id,
        to_contract_id=event.to_contract_id,
        rolled_at=event.rolled_at,
        ratio=event.ratio,
        available_at=event.available_at,
    )


async def _select(
    session: AsyncSession, root_id: int | None, contract_id: int | None, timeframe: str
) -> None:
    if (root_id is None) == (contract_id is None):
        raise HTTPException(422, "Укажите ровно один из: root_id или contract_id")
    if timeframe != "1m" and timeframe not in TIMEFRAMES:
        raise HTTPException(
            422, f"Неизвестный таймфрейм {timeframe!r}: 1m, {', '.join(TIMEFRAMES)}"
        )
    if root_id is not None:
        if timeframe == "1m":
            raise HTTPException(
                422, "Сырые минуты (1m) доступны только по contract_id, не по root"
            )
        if await session.get(Root, root_id) is None:
            raise HTTPException(404, "Root не найден")
    elif await session.get(Contract, contract_id) is None:
        raise HTTPException(404, "Контракт не найден")


async def _fetch(
    session: AsyncSession,
    *,
    root_id: int | None,
    contract_id: int | None,
    timeframe: str,
    start: datetime | None,
    end: datetime | None,
    as_of: datetime | None,
    limit: int,
    tail: bool,
    dataset_version_id: int | None = None,
) -> CandlesOut:
    await _select(session, root_id, contract_id, timeframe)
    fetch_limit = limit + 1  # лишняя строка — признак «есть ещё»
    candles: list[CandleOut]
    version_id: int | None = None
    rolls: list[RollOut] = []

    if root_id is not None:
        bars = await session.run_sync(
            lambda sync: read_continuous(
                sync,
                root_id,
                timeframe,
                start,
                end,
                as_of=as_of,
                limit=fetch_limit,
                tail=tail,
            )
        )
        candles = [
            CandleOut(
                timestamp=item.bar.timestamp,
                close_time=item.bar.close_time,
                open=item.bar.open,
                high=item.bar.high,
                low=item.bar.low,
                close=item.bar.close,
                volume=item.bar.volume,
                trade_count=item.bar.trade_count,
                is_partial=item.bar.is_partial,
                trading_day=None,
                contract_id=item.contract_id,
                price_factor=item.factor,
            )
            for item in bars
        ]
    elif timeframe == "1m":
        assert contract_id is not None
        if dataset_version_id is None:
            latest = await session.run_sync(
                lambda sync: latest_dataset_version(sync, contract_id, "1m")
            )
            if latest is None:
                raise HTTPException(404, "У контракта нет минутных данных")
            version_id = latest.id
        else:
            version = await session.get(DatasetVersion, dataset_version_id)
            if version is None or version.contract_id != contract_id:
                raise HTTPException(404, "Версия датасета не найдена у этого контракта")
            version_id = version.id
        raw_end = end
        if as_of is not None:
            closed = as_of - MINUTE + timedelta(microseconds=1)
            raw_end = closed if raw_end is None else min(raw_end, closed)
        minute_candles = await session.run_sync(
            lambda sync: read_candles(
                sync,
                version_id,
                start=start,
                end=raw_end,
                limit=fetch_limit,
                tail=tail,
            )
        )
        candles = [
            CandleOut(
                timestamp=c.timestamp,
                close_time=c.timestamp + MINUTE,
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=c.volume,
                trade_count=c.trade_count,
                is_partial=False,
            )
            for c in minute_candles
        ]
    else:
        assert contract_id is not None
        if dataset_version_id is not None:
            raise HTTPException(
                409,
                "Бары TF построены только из последней версии датасета; "
                "dataset_version_id поддерживается для сырых минут (1m)",
            )
        derived = await session.run_sync(
            lambda sync: read_bars(
                sync,
                contract_id,
                timeframe,
                start,
                end,
                closed_until=as_of,
                limit=fetch_limit,
                tail=tail,
            )
        )
        candles = [
            CandleOut(
                timestamp=b.timestamp,
                close_time=b.close_time,
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
                trade_count=b.trade_count,
                is_partial=b.is_partial,
                trading_day=b.trading_day,
            )
            for b in derived
        ]

    truncated = len(candles) > limit
    if truncated:
        candles = candles[1:] if tail else candles[:limit]
    next_start = None
    if truncated and not tail:
        next_start = _next_start(candles, timeframe)

    if root_id is not None and candles:
        first, last = candles[0].timestamp, candles[-1].timestamp
        events = await session.run_sync(lambda sync: load_rolls(sync, root_id, as_of))
        rolls = [_roll_out(e) for e in events if first <= e.rolled_at <= last]

    return CandlesOut(
        timeframe=timeframe,
        root_id=root_id,
        contract_id=contract_id,
        dataset_version_id=version_id,
        as_of=as_of,
        count=len(candles),
        truncated=truncated,
        next_start=next_start,
        candles=candles,
        rolls=rolls,
    )


def _next_start(candles: list[CandleOut], timeframe: str) -> datetime:
    """Момент сразу после последнего выданного бара (безопасно продолжать с него)."""
    last = candles[-1]
    return last.close_time if timeframe != "1m" else last.timestamp + MINUTE


@router.get(
    "/candles",
    response_model=CandlesOut,
    operation_id="getCandles",
    summary="Свечи контракта или continuous-серии",
    description=(
        "`contract_id` — бары контракта в его ценах (`1m` — сырые минуты версии "
        "датасета, по умолчанию последней). `root_id` — continuous-серия: цены "
        "приведены к масштабу текущего контракта, у каждого бара указан контракт и "
        "`price_factor`, в ответе — роллы диапазона. Выдача по возрастанию времени, "
        "не более `limit`; при `truncated` продолжайте с `next_start`. С "
        "`tail=true` — последние `limit` баров диапазона (при `truncated` "
        "слева есть более ранние)."
    ),
    responses={
        **NOT_FOUND,
        **BAD_SELECTION,
        409: {"description": "dataset_version_id для TF"},
    },
)
async def get_candles(
    session: DbSession,
    timeframe: Annotated[str, Query(description="1m, 15m, 1h, 4h, 1d, 1w")],
    root_id: int | None = None,
    contract_id: int | None = None,
    start: Annotated[AwareDatetime | None, Query(description="Включительно")] = None,
    end: Annotated[AwareDatetime | None, Query(description="Исключительно")] = None,
    dataset_version_id: Annotated[
        int | None, Query(description="Только 1m; по умолчанию последняя версия")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    tail: Annotated[
        bool,
        Query(description="Последние `limit` баров диапазона (для подгрузки истории)"),
    ] = False,
) -> CandlesOut:
    return await _fetch(
        session,
        root_id=root_id,
        contract_id=contract_id,
        timeframe=timeframe,
        start=start,
        end=end,
        as_of=None,
        limit=limit,
        tail=tail,
        dataset_version_id=dataset_version_id,
    )


@router.get(
    "/snapshot",
    response_model=CandlesOut,
    operation_id="getSnapshot",
    summary="Свечи, известные на момент as_of",
    description=(
        "Срез знания: только бары, закрытые к `as_of` (`close_time <= as_of`), и "
        "только роллы, известные к этому моменту — масштаб continuous такой, каким "
        "его видел бы наблюдатель в момент `as_of`. Возвращает **последние** "
        "`limit` баров (по возрастанию). Основа для воспроизводимых расчётов без "
        "заглядывания в будущее."
    ),
    responses={**NOT_FOUND, **BAD_SELECTION},
)
async def get_snapshot(
    session: DbSession,
    timeframe: Annotated[str, Query(description="1m, 15m, 1h, 4h, 1d, 1w")],
    as_of: Annotated[
        AwareDatetime, Query(description="Момент знания (UTC или со смещением)")
    ],
    root_id: int | None = None,
    contract_id: int | None = None,
    start: Annotated[AwareDatetime | None, Query(description="Включительно")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> CandlesOut:
    return await _fetch(
        session,
        root_id=root_id,
        contract_id=contract_id,
        timeframe=timeframe,
        start=start,
        end=None,
        as_of=as_of,
        limit=limit,
        tail=True,
    )
