import os
from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session, sessionmaker

from trader_db import create_instrument, make_engine, upgrade_head
from trader_db.testing import temporary_database


@pytest.fixture
def temp_database_url() -> Iterator[str]:
    """Пустая временная БД на сервере из TRADER_DATABASE_URL."""
    base = os.environ.get("TRADER_DATABASE_URL")
    if not base:
        pytest.skip("TRADER_DATABASE_URL не задан")
    with temporary_database(base) as url:
        yield url


@pytest.fixture
def session(temp_database_url: str) -> Iterator[Session]:
    """Сессия на временной БД, уже мигрированной до head."""
    upgrade_head(temp_database_url)
    engine = make_engine(temp_database_url)
    with Session(engine) as session:
        yield session
    engine.dispose()


def make_instrument(session: Session, ticker: str = "SBER") -> int:
    return create_instrument(
        session,
        ticker=ticker,
        name="Сбербанк",
        currency="RUB",
        tick_size=Decimal("0.01"),
        timezone="Europe/Moscow",
        source="test",
    ).id


@pytest.fixture
def instrument_id(session: Session) -> int:
    """Инструмент SBER в мигрированной БД."""
    return make_instrument(session)


@pytest.fixture
def session_factory(temp_database_url: str) -> Iterator[sessionmaker[Session]]:
    """Фабрика сессий на временной мигрированной БД (для многотранзакционных тестов)."""
    upgrade_head(temp_database_url)
    engine = make_engine(temp_database_url)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def committed_instrument_id(session_factory: sessionmaker[Session]) -> int:
    """Инструмент SBER, зафиксированный в БД (виден другим транзакциям)."""
    with session_factory() as session:
        instrument_id = make_instrument(session)
        session.commit()
    return instrument_id
