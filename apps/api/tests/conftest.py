"""Unit tests configuration module."""

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from trader_db.testing import temporary_database

from trader_api.main import create_app


@pytest.fixture
def database_url() -> Iterator[str]:
    """Пустая временная БД (нужен TRADER_DATABASE_URL); api мигрирует её сам."""
    base = os.environ.get("TRADER_DATABASE_URL")
    if not base:
        pytest.skip("TRADER_DATABASE_URL не задан")
    with temporary_database(base) as url:
        yield url


@pytest.fixture
def client(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("TRADER_DATABASE_URL", database_url)
    with TestClient(create_app(job_poll_interval=0.02)) as client:
        yield client
