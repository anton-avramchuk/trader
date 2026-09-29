import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import async_sessionmaker
from trader_db import check_connection, make_async_engine, upgrade_head

from trader_api.jobs import router as jobs_router

DbCheck = Callable[[], Awaitable[bool]]


def get_db_check(request: Request) -> DbCheck:
    engine = request.app.state.engine
    return lambda: check_connection(engine)


def create_app(
    *, migrate_on_startup: bool = True, job_poll_interval: float = 0.5
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if migrate_on_startup:
            await asyncio.to_thread(upgrade_head)
        app.state.engine = make_async_engine()
        app.state.sessions = async_sessionmaker(
            app.state.engine, expire_on_commit=False
        )
        try:
            yield
        finally:
            await app.state.engine.dispose()

    app = FastAPI(title="Trader API", lifespan=lifespan)
    app.state.job_poll_interval = job_poll_interval
    app.include_router(jobs_router)

    @app.get("/health")
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
