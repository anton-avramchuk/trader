"""REST инструментов и загрузки свечей из importer (ADR-0028).

Инструмент создаётся по тикеру из списка importer; свечи загружаются задачей
``candles.load`` за выбранный период. Сам importer — отдельный сервис: API только
спрашивает у него список тикеров и параметры инструмента.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from trader_db import enqueue_statement, has_active_instrument_job
from trader_db.models import Candle, CandleLoad, Instrument, Job
from trader_engine.timeframes import TIMEFRAMES

from trader_api.deps import DbSession
from trader_api.jobs import JobOut

router = APIRouter(tags=["instruments"])

LOAD_JOB_TYPE = "candles.load"
NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Не найдено"}}
CONFLICT: dict[int | str, dict[str, Any]] = {409: {"description": "Конфликт"}}
IMPORTER_DOWN: dict[int | str, dict[str, Any]] = {
    502: {"description": "Importer недоступен"}
}


def get_importer(request: Request) -> httpx.AsyncClient:
    client: httpx.AsyncClient = request.app.state.importer
    return client


Importer = Annotated[httpx.AsyncClient, Depends(get_importer)]


class Orm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CoverageOut(BaseModel):
    timeframe: str
    count: int
    first: datetime | None = Field(description="Открытие первой свечи")
    last: datetime | None = Field(description="Закрытие последней свечи")


class InstrumentOut(Orm):
    id: int
    ticker: str
    name: str
    currency: str
    tick_size: Decimal
    timezone: str
    tick_value: Decimal | None = Field(
        description="Стоимость тика в валюте счёта на единицу (для денег в бэктесте)"
    )
    source: str
    created_at: datetime
    coverage: list[CoverageOut] = Field(default_factory=list[CoverageOut])


class InstrumentCreate(BaseModel):
    ticker: str = Field(min_length=1, max_length=64, description="Тикер из importer")
    tick_value: Decimal | None = Field(default=None, gt=0)


class InstrumentPatch(BaseModel):
    tick_value: Decimal | None = Field(
        default=None, gt=0, description="`null` — убрать (деньги не считаются)"
    )


class TickerOut(BaseModel):
    ticker: str
    name: str
    currency: str
    tick_size: str
    timezone: str
    timeframes: list[str]
    first_date: date | None = None
    last_date: date | None = None
    added: bool = Field(default=False, description="Уже заведён как инструмент")


class LoadRequest(BaseModel):
    period_from: date | datetime = Field(
        description="Начало периода (дата — начало дня в зоне инструмента)"
    )
    period_to: date | datetime = Field(description="Конец периода, исключительно")
    timeframes: list[str] | None = Field(
        default=None, description="По умолчанию все: 15m, 1h, 4h, 1d, 1w"
    )

    @model_validator(mode="after")
    def _check(self) -> "LoadRequest":
        if self.timeframes is not None:
            unknown = [tf for tf in self.timeframes if tf not in TIMEFRAMES]
            if unknown or not self.timeframes:
                raise ValueError(
                    f"Таймфреймы: {', '.join(TIMEFRAMES)}; получено {self.timeframes}"
                )
        return self


class LoadOut(Orm):
    id: int
    timeframe_code: str
    period_from: datetime
    period_to: datetime
    rows: int
    source: str
    job_id: int | None
    created_at: datetime


async def _importer_get(importer: httpx.AsyncClient, path: str) -> Any:
    try:
        response = await importer.get(path)
    except httpx.HTTPError as error:
        raise HTTPException(502, f"Importer недоступен: {error}") from error
    if response.status_code == 404:
        raise HTTPException(404, "Тикер не найден в importer")
    if response.status_code >= 400:
        raise HTTPException(502, f"Importer ответил {response.status_code}")
    return response.json()


async def _coverage(session: DbSession, ids: list[int]) -> dict[int, list[CoverageOut]]:
    if not ids:
        return {}
    rows = await session.execute(
        select(
            Candle.instrument_id,
            Candle.timeframe_code,
            func.count(),
            func.min(Candle.open_time),
            func.max(Candle.close_time),
        )
        .where(Candle.instrument_id.in_(ids))
        .group_by(Candle.instrument_id, Candle.timeframe_code)
    )
    found: dict[int, list[CoverageOut]] = {}
    order = list(TIMEFRAMES)
    for instrument_id, timeframe, count, first, last in rows.all():
        found.setdefault(instrument_id, []).append(
            CoverageOut(timeframe=timeframe, count=count, first=first, last=last)
        )
    for items in found.values():
        items.sort(key=lambda c: order.index(c.timeframe))
    return found


def _out(instrument: Instrument, coverage: list[CoverageOut]) -> InstrumentOut:
    out = InstrumentOut.model_validate(instrument)
    out.coverage = coverage
    return out


@router.get(
    "/importer/tickers",
    response_model=list[TickerOut],
    operation_id="listImporterTickers",
    summary="Тикеры importer",
    description=(
        "Список инструментов, которые умеет отдавать importer; `added` — уже "
        "заведённые в системе."
    ),
    responses=IMPORTER_DOWN,
)
async def importer_tickers(session: DbSession, importer: Importer) -> list[TickerOut]:
    tickers = await _importer_get(importer, "/tickers")
    known = set(await session.scalars(select(Instrument.ticker)))
    return [TickerOut(**t, added=t["ticker"] in known) for t in tickers]


@router.get(
    "/instruments",
    response_model=list[InstrumentOut],
    operation_id="listInstruments",
    summary="Инструменты",
    description="Заведённые инструменты и покрытие свечами по таймфреймам.",
)
async def list_instruments(session: DbSession) -> list[InstrumentOut]:
    instruments = list(
        await session.scalars(select(Instrument).order_by(Instrument.ticker))
    )
    coverage = await _coverage(session, [i.id for i in instruments])
    return [_out(i, coverage.get(i.id, [])) for i in instruments]


@router.post(
    "/instruments",
    response_model=InstrumentOut,
    status_code=201,
    operation_id="createInstrument",
    summary="Завести инструмент по тикеру importer",
    description=(
        "Название, валюта, шаг цены и часовой пояс берутся у importer. Свечи "
        "загружаются отдельно: `POST /instruments/{id}/load`."
    ),
    responses={**NOT_FOUND, **CONFLICT, **IMPORTER_DOWN},
)
async def create_instrument(
    body: InstrumentCreate, session: DbSession, importer: Importer
) -> InstrumentOut:
    info = await _importer_get(importer, f"/tickers/{body.ticker}")
    instrument = Instrument(
        ticker=info["ticker"],
        name=info["name"],
        currency=info["currency"],
        tick_size=Decimal(str(info["tick_size"])),
        timezone=info["timezone"],
        tick_value=body.tick_value,
        source="importer",
    )
    session.add(instrument)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(409, f"Инструмент {body.ticker} уже заведён") from error
    await session.refresh(instrument)
    return _out(instrument, [])


@router.patch(
    "/instruments/{instrument_id}",
    response_model=InstrumentOut,
    operation_id="updateInstrument",
    summary="Изменить стоимость тика",
    responses=NOT_FOUND,
)
async def update_instrument(
    instrument_id: int, body: InstrumentPatch, session: DbSession
) -> InstrumentOut:
    instrument = await session.get(Instrument, instrument_id)
    if instrument is None:
        raise HTTPException(404, "Инструмент не найден")
    instrument.tick_value = body.tick_value
    await session.commit()
    coverage = await _coverage(session, [instrument_id])
    return _out(instrument, coverage.get(instrument_id, []))


@router.post(
    "/instruments/{instrument_id}/load",
    response_model=JobOut,
    status_code=201,
    operation_id="loadCandles",
    summary="Подгрузить свечи из importer",
    description=(
        "Ставит задачу `candles.load` за период `[period_from, period_to)`: worker "
        "забирает готовые свечи у importer и записывает их поверх существующих. "
        "Ход — `GET /jobs/{id}` или WebSocket. Пока по инструменту идёт загрузка, "
        "новая не ставится."
    ),
    responses={**NOT_FOUND, **CONFLICT, 422: {"description": "Неверный период"}},
)
async def load_candles(
    instrument_id: int, body: LoadRequest, session: DbSession
) -> Job:
    if await session.get(Instrument, instrument_id) is None:
        raise HTTPException(404, "Инструмент не найден")
    start = body.period_from
    end = body.period_to
    if isinstance(start, datetime) != isinstance(end, datetime):
        raise HTTPException(
            422, "period_from и period_to — обе даты или оба момента времени"
        )
    if start >= end:  # type: ignore[operator]
        raise HTTPException(422, "period_from должен быть раньше period_to")
    active = await session.run_sync(
        lambda sync: has_active_instrument_job(sync, LOAD_JOB_TYPE, instrument_id)
    )
    if active:
        raise HTTPException(409, "По инструменту уже идёт загрузка")
    params: dict[str, Any] = {
        "instrument_id": instrument_id,
        "period_from": start.isoformat(),
        "period_to": end.isoformat(),
    }
    if body.timeframes:
        params["timeframes"] = body.timeframes
    job = (await session.scalars(enqueue_statement(LOAD_JOB_TYPE, params))).one()
    await session.commit()
    return job


@router.get(
    "/instruments/{instrument_id}/loads",
    response_model=list[LoadOut],
    operation_id="listCandleLoads",
    summary="Журнал загрузок свечей",
    description="Что и за какой период загружено; новые первыми.",
    responses=NOT_FOUND,
)
async def candle_loads(
    instrument_id: int,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[CandleLoad]:
    if await session.get(Instrument, instrument_id) is None:
        raise HTTPException(404, "Инструмент не найден")
    rows = await session.scalars(
        select(CandleLoad)
        .where(CandleLoad.instrument_id == instrument_id)
        .order_by(CandleLoad.id.desc())
        .limit(limit)
    )
    return list(rows)
