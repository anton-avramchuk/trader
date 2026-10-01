"""REST ручных сеток Фибоначчи (нужна БД)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.seeding import Seed

START = {"time": "2026-09-28T10:00:00Z", "price": 120.0}
END = {"time": "2026-09-28T12:00:00Z", "price": 100.0}


def body(seed: Seed, **extra: Any) -> dict[str, Any]:
    return {
        "instrument_id": seed.id,
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
    assert client.get("/fib-grids", params={"instrument_id": seed.id}).json() == [grid]
    assert client.get("/fib-grids", params={"instrument_id": seed.id + 1}).json() == []
    assert client.delete(f"/fib-grids/{grid['id']}").status_code == 204
    assert client.get("/fib-grids").json() == []
    assert client.delete(f"/fib-grids/{grid['id']}").status_code == 404


def test_instrument_and_timeframe_filter(client: TestClient, seed: Seed) -> None:
    assert client.post("/fib-grids", json=body(seed)).status_code == 201

    assert len(client.get("/fib-grids", params={"instrument_id": seed.id}).json()) == 1
    assert client.get("/fib-grids", params={"timeframe": "1h"}).json() == []


@pytest.mark.parametrize(
    "change",
    [
        {"instrument_id": None},
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


def test_unknown_instrument_is_404(client: TestClient, seed: Seed) -> None:
    unknown = body(seed) | {"instrument_id": 99999}

    assert client.post("/fib-grids", json=unknown).status_code == 404
