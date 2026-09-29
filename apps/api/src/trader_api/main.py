import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from trader_db import check_connection, make_async_engine, upgrade_head

DbCheck = Callable[[], Awaitable[bool]]


def get_db_check(request: Request) -> DbCheck:
    engine = request.app.state.engine
    return lambda: check_connection(engine)


def create_app(*, migrate_on_startup: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if migrate_on_startup:
            await asyncio.to_thread(upgrade_head)
        app.state.engine = make_async_engine()
        try:
            yield
        finally:
            await app.state.engine.dispose()

    app = FastAPI(title="Trader API", lifespan=lifespan)

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
