"""Unit tests configuration module."""

import os
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from trader_db import make_engine, upgrade_head
from trader_db.models import Contract, Root, TradingCalendar
from trader_db.testing import temporary_database

from tests.fake_provider import MON, NOW, TODAY, TUE, WED, FakeProvider, contract
from trader_worker.handlers import HandlerRegistry
from trader_worker.registry import build_registry
from trader_worker.runner import Worker, WorkerConfig


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    """Сессии на временной мигрированной БД (нужен TRADER_DATABASE_URL)."""
    base = os.environ.get("TRADER_DATABASE_URL")
    if not base:
        pytest.skip("TRADER_DATABASE_URL не задан")
    with temporary_database(base) as url:
        upgrade_head(url)
        engine = make_engine(url)
        yield sessionmaker(engine, expire_on_commit=False)
        engine.dispose()


@pytest.fixture
def registry() -> HandlerRegistry:
    return HandlerRegistry()


@pytest.fixture
def worker(session_factory: sessionmaker[Session], registry: HandlerRegistry) -> Worker:
    return Worker(
        session_factory,
        registry,
        WorkerConfig(
            worker_id="w1",
            poll_interval=0.05,
            heartbeat_interval=0.05,
            stale_after=1.0,
            housekeeping_interval=0.05,
        ),
    )


@pytest.fixture
def contract_id(session_factory: sessionmaker[Session]) -> int:
    with session_factory() as session:
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
        contract_id = contract.id
        session.commit()
    return contract_id


@pytest.fixture
def import_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "imports"
    directory.mkdir()
    return directory


@pytest.fixture
def file_worker(
    session_factory: sessionmaker[Session], import_dir: Path, worker: Worker
) -> Worker:
    """Worker с реальным реестром: демо-задачи и ``import.file``."""
    return Worker(
        session_factory, build_registry(session_factory, import_dir), worker.config
    )


@pytest.fixture
def root_id(session_factory: sessionmaker[Session]) -> int:
    with session_factory() as session:
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
        root_id = root.id
        session.commit()
    return root_id


@pytest.fixture
def provider() -> FakeProvider:
    fake = FakeProvider(
        [
            contract("BRX6", date(2026, 11, 2)),
            contract("BRZ6", date(2026, 12, 1)),
            contract("NGZ6", date(2026, 12, 29), asset="NG"),
        ]
    )
    fake.set_days("BRZ6", [MON, TUE, WED])
    return fake


@pytest.fixture
def iss_worker(
    worker: Worker,
    session_factory: sessionmaker[Session],
    provider: FakeProvider,
    tmp_path: Path,
) -> Worker:
    registry = build_registry(
        session_factory,
        tmp_path,
        provider.factory,
        chunk_days=2,
        today=lambda: TODAY,
        clock=lambda: NOW,
    )
    return Worker(session_factory, registry, worker.config)
