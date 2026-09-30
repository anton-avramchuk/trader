"""REST ручных сеток Фибоначчи (нужна БД)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.seeding import Seed

START = {"time": "2026-09-28T10:00:00Z", "price": 120.0}
END = {"time": "2026-09-28T12:00:00Z", "price": 100.0}


def body(seed: Seed, **extra: Any) -> dict[str, Any]:
    return {
        "contract_id": seed.a,
        "timeframe": "15m",
        "start": START,
        "end": END,
        **extra,
    }


def test_create_list_delete(client: TestClient, seed: Seed) -> None:
    created = client.post("/fib-grids", json=body(seed, label="ручная"))

    assert created.status_code == 201, created.text
    grid = created.json()
    assert grid["manual"] is True and grid["direction"] == "down"
    assert grid["retracement"]["38.2"] == pytest.approx(107.64)
    assert grid["extension"]["161.8"] == pytest.approx(87.64)
    assert client.get("/fib-grids", params={"contract_id": seed.a}).json() == [grid]
    assert client.get("/fib-grids", params={"contract_id": seed.b}).json() == []
    assert client.delete(f"/fib-grids/{grid['id']}").status_code == 204
    assert client.get("/fib-grids").json() == []
    assert client.delete(f"/fib-grids/{grid['id']}").status_code == 404


def test_root_series_and_timeframe_filter(client: TestClient, seed: Seed) -> None:
    payload = body(seed) | {"contract_id": None, "root_id": seed.root_id}
    assert client.post("/fib-grids", json=payload).status_code == 201

    assert len(client.get("/fib-grids", params={"root_id": seed.root_id}).json()) == 1
    assert client.get("/fib-grids", params={"timeframe": "1h"}).json() == []


@pytest.mark.parametrize(
    "change",
    [
        {"contract_id": None},
        {"root_id": 1},
        {"timeframe": "2h"},
        {"end": START},
        {"end": {"time": "2026-09-28T12:00:00Z", "price": 120.0}},
        {"end": {"time": "2026-09-28T12:00:00", "price": 100.0}},
    ],
)
def test_invalid_body_is_422(
    client: TestClient, seed: Seed, change: dict[str, Any]
) -> None:
    assert client.post("/fib-grids", json=body(seed) | change).status_code == 422


def test_unknown_subject_is_404(client: TestClient, seed: Seed) -> None:
    unknown_contract = body(seed) | {"contract_id": 99999}
    unknown_root = body(seed) | {"contract_id": None, "root_id": 99999}

    assert client.post("/fib-grids", json=unknown_contract).status_code == 404
    assert client.post("/fib-grids", json=unknown_root).status_code == 404
