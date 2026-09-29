import pytest
from sqlalchemy.engine import make_url

from trader_db import DbSettings, make_async_engine, make_engine


def test_default_url_points_to_localhost() -> None:
    assert make_url(DbSettings().database_url).host == "127.0.0.1"


def test_url_is_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADER_DATABASE_URL", "postgresql+psycopg://u:p@db:5432/x")

    assert DbSettings().database_url == "postgresql+psycopg://u:p@db:5432/x"


def test_sync_engine_uses_psycopg() -> None:
    engine = make_engine("postgresql+psycopg://u:p@db:5432/x")

    assert engine.url.drivername == "postgresql+psycopg"


def test_async_engine_uses_asyncpg_with_same_target() -> None:
    engine = make_async_engine("postgresql+psycopg://u:p@db:5432/x")

    assert engine.url.drivername == "postgresql+asyncpg"
    assert (engine.url.host, engine.url.database) == ("db", "x")
