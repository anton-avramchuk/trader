import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from threading import Lock
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import async_sessionmaker
from trader_db import check_connection, make_async_engine, upgrade_head
from trader_engine.indicators import IndicatorCache

from trader_api.analogues_api import router as analogues_router
from trader_api.backtests_api import router as backtests_router
from trader_api.candles_api import router as candles_router
from trader_api.deps import ApiSettings
from trader_api.engines_api import router as engines_router
from trader_api.fib_grids_api import router as fib_grids_router
from trader_api.forecast_api import router as forecast_router
from trader_api.indicators_api import router as indicators_router
from trader_api.instruments_api import router as instruments_router
from trader_api.jobs import router as jobs_router
from trader_api.profiles_api import router as profiles_router
from trader_api.stats_api import router as stats_router
from trader_api.zones_api import router as zones_router

DbCheck = Callable[[], Awaitable[bool]]


def get_db_check(request: Request) -> DbCheck:
    engine = request.app.state.engine
    return lambda: check_connection(engine)


def create_app(
    *,
    migrate_on_startup: bool = True,
    job_poll_interval: float = 0.5,
    importer_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if migrate_on_startup:
            await asyncio.to_thread(upgrade_head)
        app.state.engine = make_async_engine()
        app.state.sessions = async_sessionmaker(
            app.state.engine, expire_on_commit=False
        )
        app.state.importer = httpx.AsyncClient(
            base_url=ApiSettings().importer_url,
            transport=importer_transport,
            timeout=30.0,
        )
        try:
            yield
        finally:
            await app.state.importer.aclose()
            await app.state.engine.dispose()

    app = FastAPI(
        title="Trader API",
        version="0.1.0",
        summary="Исследовательская платформа по рыночным данным",
        description=(
            "REST API платформы. Документация: `/docs` (Swagger UI), `/redoc`, "
            "схема — `/openapi.json`.\n\n"
            "**WebSocket** `/ws/jobs/{job_id}` (в OpenAPI не описывается): шлёт JSON "
            "в формате `JobOut` при каждом изменении задачи и закрывается по её "
            "завершении; код закрытия 4404 — задачи нет."
        ),
        openapi_tags=[
            {
                "name": "jobs",
                "description": "Фоновые задачи: загрузка свечей, прогоны, бэктесты.",
            },
            {
                "name": "instruments",
                "description": "Инструменты и загрузка свечей из importer.",
            },
            {
                "name": "candles",
                "description": "Свечи инструмента и snapshot as-of.",
            },
            {"name": "indicators", "description": "Индикаторы и MTF-проекция."},
            {"name": "profiles", "description": "Профили графика (индикаторы, слои)."},
            {
                "name": "engines",
                "description": "Движки событий: прогоны и неизменяемый лог событий.",
            },
            {"name": "system", "description": "Служебные проверки."},
        ],
        lifespan=lifespan,
    )
    app.state.job_poll_interval = job_poll_interval
    app.state.indicator_cache = IndicatorCache()
    app.state.indicator_lock = Lock()
    app.include_router(jobs_router)
    app.include_router(instruments_router)
    app.include_router(candles_router)
    app.include_router(indicators_router)
    app.include_router(profiles_router)
    app.include_router(engines_router)
    app.include_router(fib_grids_router)
    app.include_router(zones_router)
    app.include_router(stats_router)
    app.include_router(analogues_router)
    app.include_router(forecast_router)
    app.include_router(backtests_router)

    @app.get(
        "/health",
        tags=["system"],
        operation_id="getHealth",
        summary="Проверка работоспособности",
        description="200 — API и база доступны, 503 — база недоступна.",
        responses={503: {"description": "База данных недоступна"}},
    )
    async def health(
        db_check: Annotated[DbCheck, Depends(get_db_check)],
    ) -> JSONResponse:
        database_ok = await db_check()
        return JSONResponse(
            status_code=200 if database_ok else 503,
            content={
                "status": "ok" if database_ok else "degraded",
                "database": "ok" if database_ok else "unavailable",
            },
        )

    return app


app = create_app()
