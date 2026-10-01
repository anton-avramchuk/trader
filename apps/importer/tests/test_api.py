"""HTTP-контракт importer на фейковом источнике."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from trader_importer.main import create_app
from trader_importer.models import Candle, HistoryPage, Ticker, Timeframe
from trader_importer.source import SourceError, UnknownTicker

T = datetime(2026, 9, 28, 7, tzinfo=UTC)


class FakeSource:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, datetime, datetime, int]] = []
        self.closed = False
        self.broken = False

    def tickers(self) -> list[Ticker]:
        if self.broken:
            raise SourceError("ISS недоступен")
        return [
            Ticker(
                ticker="SBER",
                name="Сбербанк",
                currency="RUB",
                tick_size=Decimal("0.01"),
                timezone="Europe/Moscow",
                timeframes=["15m", "1h"],
            )
        ]

    def ticker(self, ticker: str) -> Ticker:
        if ticker != "SBER":
            raise UnknownTicker(ticker)
        return self.tickers()[0]

    def history(
        self, ticker: str, tf: Timeframe, start: datetime, end: datetime, limit: int
    ) -> HistoryPage:
        self.calls.append((ticker, tf, start, end, limit))
        if ticker != "SBER":
            raise UnknownTicker(ticker)
        candle = Candle(
            t=T,
            o=Decimal("100.5"),
            h=Decimal("101"),
            l=Decimal("100"),
            c=Decimal("100.75"),
            v=Decimal("12"),
        )
        return HistoryPage(ticker=ticker, tf=tf, candles=[candle], next_from=None)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def source() -> FakeSource:
    return FakeSource()


@pytest.fixture
def client(source: FakeSource) -> TestClient:
    with TestClient(create_app(source)) as client:
        return client


def history(client: TestClient, **params: str | int) -> object:
    base: dict[str, str | int] = {
        "ticker": "SBER",
        "tf": "15m",
        "from": "2026-09-28T00:00:00Z",
        "to": "2026-09-29T00:00:00Z",
    }
    return client.get("/history", params=base | params)


def test_health_and_tickers(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok", "contract": "1"}
    [item] = client.get("/tickers").json()
    assert item["ticker"] == "SBER" and item["tick_size"] == "0.01"
    assert item["timeframes"] == ["15m", "1h"]
    assert client.get("/tickers/SBER").json()["name"] == "Сбербанк"
    assert client.get("/tickers/NOPE").status_code == 404


def test_history_contract_shape(client: TestClient, source: FakeSource) -> None:
    body = history(client).json()  # type: ignore[attr-defined]

    assert body["ticker"] == "SBER" and body["tf"] == "15m"
    assert body["next_from"] is None
    assert body["candles"] == [
        {
            "t": "2026-09-28T07:00:00Z",
            "o": "100.5",
            "h": "101",
            "l": "100",
            "c": "100.75",
            "v": "12",
        }
    ]
    ticker, tf, start, end, limit = source.calls[0]
    assert (ticker, tf, limit) == ("SBER", "15m", 5000)
    assert start == datetime(2026, 9, 28, tzinfo=UTC) and end > start


def test_history_validation_and_errors(client: TestClient, source: FakeSource) -> None:
    assert history(client, tf="2h").status_code == 422  # type: ignore[attr-defined]
    assert history(client, limit=0).status_code == 422  # type: ignore[attr-defined]
    assert history(client, limit=20001).status_code == 422  # type: ignore[attr-defined]
    assert history(client, **{"from": "2026-09-30T00:00:00Z"}).status_code == 422  # type: ignore[attr-defined]
    assert history(client, **{"from": "2026-09-28T00:00:00"}).status_code == 422  # type: ignore[attr-defined]  # без зоны
    assert history(client, ticker="NOPE").status_code == 404  # type: ignore[attr-defined]
    assert client.get("/history").status_code == 422
    source.broken = True
    assert client.get("/tickers").status_code == 502


def test_openapi_lists_the_contract(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert {"/health", "/tickers", "/tickers/{ticker}", "/history"} <= set(paths)
    params = {p["name"] for p in paths["/history"]["get"]["parameters"]}
    assert params == {"ticker", "tf", "from", "to", "limit"}
