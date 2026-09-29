import os

import pytest
from fastapi.testclient import TestClient

from trader_api.main import create_app, get_db_check


def client_with_db(available: bool) -> TestClient:
    async def db_check() -> bool:
        return available

    app = create_app(migrate_on_startup=False)
    app.dependency_overrides[get_db_check] = lambda: db_check
    return TestClient(app)


def test_health_ok_when_database_available() -> None:
    response = client_with_db(True).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_503_when_database_unavailable() -> None:
    response = client_with_db(False).get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable"}


@pytest.mark.skipif(
    not os.environ.get("TRADER_DATABASE_URL"), reason="TRADER_DATABASE_URL не задан"
)
def test_health_with_real_database_applies_migrations() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["database"] == "ok"
