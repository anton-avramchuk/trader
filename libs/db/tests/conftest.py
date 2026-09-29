import os
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from trader_db import make_engine, upgrade_head
from trader_db.models import Contract, Root, TradingCalendar
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


@pytest.fixture
def contract_id(session: Session) -> int:
    """Контракт BR-12.26 на сидовом календаре moex_forts."""
    calendar_id = session.scalars(
        select(TradingCalendar.id).where(TradingCalendar.code == "moex_forts")
    ).one()
    root = Root(
        code="BR",
        name="Brent",
        exchange="MOEX",
        quote_currency="USD",
        tick_size=Decimal("0.01"),
        calendar_id=calendar_id,
        roll_trading_days=5,
    )
    session.add(root)
    session.flush()
    contract = Contract(root_id=root.id, expiration_date=date(2026, 12, 1))
    session.add(contract)
    session.flush()
    return contract.id
