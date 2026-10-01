"""HTTP-сервис importer: ``GET /tickers``, ``GET /history`` (ADR-0028).

Сервис ничего не хранит и не знает про ядро: он отдаёт историю источника готовыми
закрытыми свечами таймфрейма. Первая реализация — прокси к MOEX ISS.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import AwareDatetime

from trader_importer.iss import IssSource
from trader_importer.models import HistoryPage, Ticker, Timeframe
from trader_importer.source import HistorySource, SourceError, UnknownTicker

MAX_LIMIT = 20_000
DEFAULT_LIMIT = 5_000
VERSION = "1"


def create_app(source: HistorySource | None = None) -> FastAPI:
    """Приложение; ``source`` подменяется в тестах (по умолчанию — ISS)."""
    chosen = source

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.source = chosen or IssSource(
            board=os.environ.get("IMPORTER_ISS_BOARD", "TQBR")
        )
        yield
        app.state.source.close()

    app = FastAPI(
        title="Trader importer",
        version=VERSION,
        description=(
            "Исторические данные по тикерам: список инструментов и готовые закрытые "
            "свечи таймфреймов 15m, 1h, 4h, 1d, 1w. Ничего не хранит."
        ),
        lifespan=lifespan,
    )

    @app.get("/health", summary="Состояние сервиса", operation_id="health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "contract": VERSION}

    @app.get(
        "/tickers",
        response_model=list[Ticker],
        summary="Список тикеров",
        operation_id="listTickers",
    )
    async def tickers() -> list[Ticker]:
        return await _call(app, lambda s: s.tickers())

    @app.get(
        "/tickers/{ticker}",
        response_model=Ticker,
        summary="Тикер с границами истории",
        operation_id="getTicker",
        responses={404: {"description": "Нет такого тикера"}},
    )
    async def one(ticker: str) -> Ticker:
        return await _call(app, lambda s: s.ticker(ticker))

    @app.get(
        "/history",
        response_model=HistoryPage,
        summary="Свечи тикера за период",
        operation_id="getHistory",
        description=(
            "Закрытые свечи таймфрейма с началом в `[from, to)` по возрастанию. "
            "Если период не уместился в `limit` (или в потолок запросов к источнику), "
            "`next_from` — с чего продолжить."
        ),
        responses={
            404: {"description": "Нет такого тикера"},
            422: {"description": "Неверные параметры"},
            502: {"description": "Источник недоступен"},
        },
    )
    async def history(
        ticker: Annotated[str, Query(min_length=1)],
        tf: Timeframe,
        from_: Annotated[AwareDatetime, Query(alias="from")],
        to: AwareDatetime,
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    ) -> HistoryPage:
        if from_ >= to:
            raise HTTPException(422, "from должен быть раньше to")
        start: datetime = from_
        end: datetime = to
        return await _call(app, lambda s: s.history(ticker, tf, start, end, limit))

    return app


async def _call(app: FastAPI, work):  # type: ignore[no-untyped-def]
    source: HistorySource = app.state.source
    try:
        return await run_in_threadpool(work, source)
    except UnknownTicker as error:
        raise HTTPException(404, f"Тикер не найден: {error}") from error
    except SourceError as error:
        raise HTTPException(502, str(error)) from error


app = create_app()
