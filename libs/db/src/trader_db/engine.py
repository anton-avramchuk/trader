from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from trader_db.settings import DbSettings


def _database_url(url: str | None) -> str:
    return url if url is not None else DbSettings().database_url


def make_engine(url: str | None = None) -> Engine:
    """Синхронный движок (psycopg) — для worker и миграций."""
    return create_engine(_database_url(url), pool_pre_ping=True)


def make_async_engine(url: str | None = None) -> AsyncEngine:
    """Асинхронный движок (asyncpg) — для API.

    asyncpg, а не psycopg: psycopg async не работает с ProactorEventLoop,
    который uvicorn использует на Windows.
    """
    async_url = make_url(_database_url(url)).set(drivername="postgresql+asyncpg")
    return create_async_engine(async_url, pool_pre_ping=True)


async def check_connection(engine: AsyncEngine) -> bool:
    """``True``, если БД отвечает на ``SELECT 1``."""
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        return False
    return True
