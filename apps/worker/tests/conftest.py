"""Unit tests configuration module."""

import os
import random
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker
from trader_db import (
    CandleRow,
    create_instrument,
    make_engine,
    upgrade_head,
    upsert_candles,
)
from trader_db.testing import temporary_database
from trader_engine.timeframes import TIMEFRAMES

from trader_worker.handlers import HandlerRegistry
from trader_worker.importer_client import ImporterClient
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
def instrument_id(session_factory: sessionmaker[Session]) -> int:
    """Инструмент SBER, зафиксированный в БД."""
    with session_factory() as session:
        instrument = create_instrument(
            session,
            ticker="SBER",
            name="Сбербанк",
            currency="RUB",
            tick_size=Decimal("0.01"),
            timezone="Europe/Moscow",
            source="test",
            tick_value=Decimal("1"),
        )
        session.commit()
        return instrument.id


def synthetic_candles(timeframe: str, count: int, seed: int = 3) -> list[CandleRow]:
    """Детерминированное блуждание цены со ступенчатыми волнами (для движков)."""
    rng = random.Random(seed)
    length = TIMEFRAMES[timeframe]
    opened = datetime(2026, 1, 5, 7, tzinfo=UTC)
    price = 100.0
    rows: list[CandleRow] = []
    for i in range(count):
        wave = 1.5 if (i // 12) % 2 == 0 else -1.5
        close = price + wave * rng.uniform(0.2, 1.0) + rng.gauss(0, 0.8)
        high = max(price, close) + rng.uniform(0, 0.8)
        low = min(price, close) - rng.uniform(0, 0.8)
        start = opened + i * length
        rows.append(
            CandleRow(
                open_time=start,
                close_time=start + length,
                open=Decimal(f"{price:.4f}"),
                high=Decimal(f"{high:.4f}"),
                low=Decimal(f"{low:.4f}"),
                close=Decimal(f"{close:.4f}"),
                volume=Decimal(rng.randint(10, 100)),
                trading_day=start.date(),
            )
        )
        price = close
    return rows


def seed_candles(
    factory: sessionmaker[Session],
    instrument: int,
    timeframe: str = "15m",
    count: int = 800,
) -> None:
    with factory() as session, session.begin():
        upsert_candles(
            session, instrument, timeframe, synthetic_candles(timeframe, count)
        )


class FakeImporter:
    """Фейковый importer: свечи по требованию; считает запросы, умеет падать."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.page_size = 3
        self.fail_with: int | None = None
        self.known = {"SBER"}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.fail_with is not None:
            return httpx.Response(self.fail_with, json={"detail": "источник упал"})
        path = request.url.path
        if path == "/tickers":
            return httpx.Response(200, json=[self._ticker("SBER")])
        if path.startswith("/tickers/"):
            ticker = path.rsplit("/", 1)[1]
            if ticker not in self.known:
                return httpx.Response(
                    404, json={"detail": f"Тикер не найден: {ticker}"}
                )
            return httpx.Response(200, json=self._ticker(ticker))
        if path == "/history":
            return self._history(request)
        return httpx.Response(404)

    @staticmethod
    def _ticker(ticker: str) -> dict[str, Any]:
        return {
            "ticker": ticker,
            "name": "Сбербанк",
            "currency": "RUB",
            "tick_size": "0.01",
            "timezone": "Europe/Moscow",
            "timeframes": list(TIMEFRAMES),
            "first_date": None,
            "last_date": None,
        }

    def _history(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if params["ticker"] not in self.known:
            return httpx.Response(404, json={"detail": "Тикер не найден"})
        length = TIMEFRAMES[params["tf"]]
        start = datetime.fromisoformat(params["from"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(params["to"].replace("Z", "+00:00"))
        slots: list[datetime] = []
        moment = start
        while moment < end:
            slots.append(moment)
            moment += length
        page = slots[: self.page_size]
        candles = [
            {
                "t": t.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "o": "100.5",
                "h": "101",
                "l": "100",
                "c": "100.75",
                "v": "12",
            }
            for t in page
        ]
        more = slots[self.page_size :]
        next_from = (
            None
            if not more
            else more[0].astimezone(UTC).isoformat().replace("+00:00", "Z")
        )
        return httpx.Response(
            200,
            json={
                "ticker": params["ticker"],
                "tf": params["tf"],
                "candles": candles,
                "next_from": next_from,
            },
        )


@pytest.fixture
def importer() -> FakeImporter:
    return FakeImporter()


@pytest.fixture
def loader_worker(
    worker: Worker, session_factory: sessionmaker[Session], importer: FakeImporter
) -> Worker:
    """Worker с реальным реестром, но с фейковым importer."""

    def factory() -> ImporterClient:
        return ImporterClient(
            client=httpx.Client(
                transport=httpx.MockTransport(importer), base_url="http://importer.test"
            )
        )

    return Worker(
        session_factory, build_registry(session_factory, factory), worker.config
    )
