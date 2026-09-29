"""Интеграционные тесты: выполняются, только если задан TRADER_DATABASE_URL."""

import os

import pytest

from trader_db import check_connection, make_async_engine, upgrade_head

pytestmark = pytest.mark.skipif(
    not os.environ.get("TRADER_DATABASE_URL"),
    reason="TRADER_DATABASE_URL не задан",
)


async def test_check_connection_ok() -> None:
    engine = make_async_engine()
    try:
        assert await check_connection(engine) is True
    finally:
        await engine.dispose()


def test_upgrade_head_is_idempotent() -> None:
    upgrade_head()
    upgrade_head()
