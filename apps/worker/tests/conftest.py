"""Unit tests configuration module."""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session, sessionmaker
from trader_db import make_engine, upgrade_head
from trader_db.testing import temporary_database

from trader_worker.handlers import HandlerRegistry
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
