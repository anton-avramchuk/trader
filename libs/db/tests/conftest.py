import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from trader_db import make_engine, upgrade_head


@pytest.fixture
def temp_database_url() -> Iterator[str]:
    """Пустая временная БД на сервере из TRADER_DATABASE_URL.

    Тесты миграций (в т.ч. downgrade) никогда не трогают рабочую БД.
    """
    base = os.environ.get("TRADER_DATABASE_URL")
    if not base:
        pytest.skip("TRADER_DATABASE_URL не задан")
    url = make_url(base)
    name = f"trader_test_{uuid4().hex[:12]}"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield url.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def session(temp_database_url: str) -> Iterator[Session]:
    """Сессия на временной БД, уже мигрированной до head."""
    upgrade_head(temp_database_url)
    engine = make_engine(temp_database_url)
    with Session(engine) as session:
        yield session
    engine.dispose()
