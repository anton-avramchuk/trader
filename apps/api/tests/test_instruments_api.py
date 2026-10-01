"""REST инструментов: тикеры importer, создание, загрузка свечей (нужна БД)."""

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from trader_db import enqueue_job, make_engine
from trader_db.models import CandleLoad

from tests.seeding import Seed
from trader_api.main import create_app

TICKERS = [
    {
        "ticker": "SBER",
        "name": "Сбербанк",
        "currency": "RUB",
        "tick_size": "0.01",
        "timezone": "Europe/Moscow",
        "timeframes": ["15m", "1h", "4h", "1d", "1w"],
        "first_date": None,
        "last_date": None,
    },
    {
        "ticker": "GAZP",
        "name": "Газпром",
        "currency": "RUB",
        "tick_size": "0.02",
        "timezone": "Europe/Moscow",
        "timeframes": ["1h", "1d"],
        "first_date": "2010-01-01",
        "last_date": "2026-10-01",
    },
]


class FakeImporterApi:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": "сбой"})
        if request.url.path == "/tickers":
            return httpx.Response(200, json=TICKERS)
        ticker = request.url.path.removeprefix("/tickers/")
        found = next((t for t in TICKERS if t["ticker"] == ticker), None)
        if found is None:
            return httpx.Response(404, json={"detail": "нет такого"})
        return httpx.Response(200, json=found)


@pytest.fixture
def importer() -> FakeImporterApi:
    return FakeImporterApi()


@pytest.fixture
def client(
    database_url: str, monkeypatch: pytest.MonkeyPatch, importer: FakeImporterApi
) -> Iterator[TestClient]:
    monkeypatch.setenv("TRADER_DATABASE_URL", database_url)
    app = create_app(
        job_poll_interval=0.02, importer_transport=httpx.MockTransport(importer)
    )
    with TestClient(app) as client:
        yield client


def create(client: TestClient, ticker: str = "SBER", **extra: Any) -> dict[str, Any]:
    response = client.post("/instruments", json={"ticker": ticker, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_importer_tickers_mark_added_instruments(client: TestClient) -> None:
    before = client.get("/importer/tickers").json()
    create(client, "GAZP")
    after = client.get("/importer/tickers").json()

    assert [t["ticker"] for t in before] == ["SBER", "GAZP"]
    assert [t["added"] for t in before] == [False, False]
    assert [t["added"] for t in after] == [False, True]
    assert after[1]["first_date"] == "2010-01-01"


def test_create_takes_parameters_from_the_importer(client: TestClient) -> None:
    created = create(client, "GAZP", tick_value="0.5")

    assert created["name"] == "Газпром" and created["currency"] == "RUB"
    assert created["tick_size"] == "0.02000000" or float(created["tick_size"]) == 0.02
    assert created["timezone"] == "Europe/Moscow" and created["source"] == "importer"
    assert float(created["tick_value"]) == 0.5 and created["coverage"] == []
    assert [i["ticker"] for i in client.get("/instruments").json()] == ["GAZP"]


def test_duplicate_unknown_and_invalid(client: TestClient) -> None:
    create(client)

    assert client.post("/instruments", json={"ticker": "SBER"}).status_code == 409
    assert client.post("/instruments", json={"ticker": "NOPE"}).status_code == 404
    assert client.post("/instruments", json={"ticker": ""}).status_code == 422
    assert (
        client.post(
            "/instruments", json={"ticker": "GAZP", "tick_value": 0}
        ).status_code
        == 422
    )


def test_importer_failure_is_a_bad_gateway(
    client: TestClient, importer: FakeImporterApi
) -> None:
    importer.status = 503

    assert client.get("/importer/tickers").status_code == 502
    assert client.post("/instruments", json={"ticker": "SBER"}).status_code == 502


def test_coverage_lists_loaded_timeframes(client: TestClient, seed: Seed) -> None:
    [instrument] = client.get("/instruments").json()

    by_tf = {c["timeframe"]: c for c in instrument["coverage"]}
    assert list(by_tf) == ["15m", "1h", "4h", "1d", "1w"]  # по порядку таймфреймов
    assert by_tf["15m"]["count"] == 144 and by_tf["1d"]["count"] == 4
    assert by_tf["15m"]["first"] < by_tf["15m"]["last"]


def test_update_tick_value(client: TestClient) -> None:
    instrument = create(client)

    updated = client.patch(
        f"/instruments/{instrument['id']}", json={"tick_value": "2.5"}
    ).json()
    cleared = client.patch(
        f"/instruments/{instrument['id']}", json={"tick_value": None}
    ).json()

    assert float(updated["tick_value"]) == 2.5 and cleared["tick_value"] is None
    assert client.patch("/instruments/999", json={}).status_code == 404
    assert (
        client.patch(
            f"/instruments/{instrument['id']}", json={"tick_value": -1}
        ).status_code
        == 422
    )


def test_load_queues_a_job_with_the_period(client: TestClient) -> None:
    instrument = create(client)

    response = client.post(
        f"/instruments/{instrument['id']}/load",
        json={
            "period_from": "2026-09-01",
            "period_to": "2026-09-30",
            "timeframes": ["1h", "1d"],
        },
    )

    assert response.status_code == 201, response.text
    job = response.json()
    assert job["type"] == "candles.load" and job["status"] == "queued"
    assert job["params"] == {
        "instrument_id": instrument["id"],
        "period_from": "2026-09-01",
        "period_to": "2026-09-30",
        "timeframes": ["1h", "1d"],
    }


def test_load_accepts_moments_and_default_timeframes(client: TestClient) -> None:
    instrument = create(client)

    job = client.post(
        f"/instruments/{instrument['id']}/load",
        json={
            "period_from": "2026-09-01T07:00:00Z",
            "period_to": "2026-09-02T07:00:00Z",
        },
    ).json()

    assert "timeframes" not in job["params"]
    assert job["params"]["period_from"].startswith("2026-09-01T07:00:00")


def test_second_load_is_rejected_while_one_is_active(client: TestClient) -> None:
    instrument = create(client)
    body = {"period_from": "2026-09-01", "period_to": "2026-09-30"}
    url = f"/instruments/{instrument['id']}/load"

    first = client.post(url, json=body)
    second = client.post(url, json=body)

    assert first.status_code == 201 and second.status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"period_from": "2026-09-30", "period_to": "2026-09-01"},
        {"period_from": "2026-09-01", "period_to": "2026-09-01"},
        {"period_from": "2026-09-01", "period_to": "2026-09-30T10:00:00Z"},
        {"period_from": "2026-09-01", "period_to": "2026-09-30", "timeframes": ["1m"]},
        {"period_from": "2026-09-01", "period_to": "2026-09-30", "timeframes": []},
        {"period_from": "когда-то", "period_to": "2026-09-30"},
        {"period_from": "2026-09-01"},
    ],
)
def test_load_validation(client: TestClient, body: dict[str, Any]) -> None:
    instrument = create(client)

    response = client.post(f"/instruments/{instrument['id']}/load", json=body)

    assert response.status_code == 422, response.text


def test_load_for_unknown_instrument_is_404(client: TestClient) -> None:
    response = client.post(
        "/instruments/999/load",
        json={"period_from": "2026-09-01", "period_to": "2026-09-30"},
    )

    assert response.status_code == 404


def test_loads_journal(client: TestClient, database_url: str) -> None:
    instrument = create(client)
    engine = make_engine(database_url)
    with Session(engine) as session:
        job_id = enqueue_job(session, "demo.sleep", {})
        from datetime import UTC, datetime

        session.add(
            CandleLoad(
                instrument_id=instrument["id"],
                timeframe_code="1h",
                period_from=datetime(2026, 9, 1, tzinfo=UTC),
                period_to=datetime(2026, 9, 30, tzinfo=UTC),
                rows=42,
                source="importer",
                job_id=job_id,
            )
        )
        session.commit()
    engine.dispose()

    [load] = client.get(f"/instruments/{instrument['id']}/loads").json()

    assert load["rows"] == 42 and load["timeframe_code"] == "1h"
    assert load["job_id"] == job_id
    assert client.get("/instruments/999/loads").status_code == 404
