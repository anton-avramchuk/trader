"""Прокси к ISS на фейковом транспорте: тикеры, свечи, агрегация, пагинация, ошибки."""

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from trader_importer.iss import PAGE, IssSource
from trader_importer.source import SourceError, UnknownTicker

MSK = ZoneInfo("Europe/Moscow")
NOW = datetime(2030, 1, 1, tzinfo=UTC)
CANDLE_COLUMNS = ["open", "close", "high", "low", "value", "volume", "begin", "end"]


def table(columns: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {"columns": columns, "data": rows}


def minute_rows(count: int, first: datetime) -> list[list[Any]]:
    rows = []
    for i in range(count):
        start = first + timedelta(minutes=i)
        rows.append(
            [
                100 + i % 3,
                101 + i % 3,
                102 + i % 3,
                99 + i % 3,
                0,
                10,
                start.strftime("%Y-%m-%d %H:%M:%S"),
                (start + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"),
            ]
        )
    return rows


class FakeIss:
    """Минутные свечи SBER с 10:00 МСК; считает запросы."""

    def __init__(self, minutes: int = 40, known: str = "SBER") -> None:
        self.first = datetime(2026, 9, 28, 10, 0)
        self.minutes = minutes
        self.known = known
        self.calls: list[httpx.Request] = []
        self.fail_with: int | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.fail_with is not None:
            return httpx.Response(self.fail_with)
        path = request.url.path
        params = request.url.params
        if path.endswith("/securities.json"):
            return httpx.Response(
                200,
                json={
                    "securities": table(
                        ["SECID", "SHORTNAME", "CURRENCYID", "MINSTEP"],
                        [
                            ["SBER", "Сбербанк", "SUR", 0.01],
                            ["GAZP", "Газпром", "SUR", 0.02],
                        ],
                    )
                },
            )
        if path.endswith(f"/securities/{self.known}.json"):
            return httpx.Response(
                200,
                json={
                    "securities": table(
                        ["SECID", "SHORTNAME", "CURRENCYID", "MINSTEP"],
                        [[self.known, "Сбербанк", "SUR", 0.01]],
                    )
                },
            )
        if path.endswith("/candleborders.json"):
            return httpx.Response(
                200,
                json={
                    "borders": table(
                        ["begin", "end", "interval", "board_group"],
                        [["2011-11-21 00:00:00", "2026-09-28 18:50:00", 24, 57]],
                    )
                },
            )
        if path.endswith("/candles.json") and f"/{self.known}/" in path:
            offset = int(params.get("start", 0))
            rows = minute_rows(self.minutes, self.first)[offset : offset + PAGE]
            if params.get("interval") == "24":
                rows = [
                    [
                        100,
                        101,
                        102,
                        99,
                        0,
                        5,
                        "2026-09-28 00:00:00",
                        "2026-09-28 23:59:59",
                    ]
                ]
            return httpx.Response(200, json={"candles": table(CANDLE_COLUMNS, rows)})
        return httpx.Response(404)


def source(fake: FakeIss) -> IssSource:
    client = httpx.Client(
        transport=httpx.MockTransport(fake), base_url="https://iss.test"
    )
    return IssSource(client, min_interval=0, clock=lambda: NOW, sleep=lambda s: None)


START = datetime(2026, 9, 28, 0, tzinfo=MSK)
END = datetime(2026, 9, 29, 0, tzinfo=MSK)


def test_tickers_are_mapped() -> None:
    items = source(FakeIss()).tickers()

    assert [t.ticker for t in items] == ["SBER", "GAZP"]
    first = items[0]
    assert first.currency == "RUB" and str(first.tick_size) == "0.01"
    assert first.timezone == "Europe/Moscow"
    assert first.timeframes == ["15m", "1h", "4h", "1d", "1w"]
    assert first.first_date is None  # границы — только в карточке тикера


def test_one_ticker_has_history_borders() -> None:
    item = source(FakeIss()).ticker("SBER")

    assert str(item.first_date) == "2011-11-21" and str(item.last_date) == "2026-09-28"
    with pytest.raises(UnknownTicker):
        source(FakeIss(known="SBER")).ticker("NOPE")


def test_15m_is_aggregated_from_minutes_in_utc() -> None:
    page = source(FakeIss(minutes=40)).history("SBER", "15m", START, END, 100)

    assert [c.t for c in page.candles] == [
        datetime(2026, 9, 28, 7, 0, tzinfo=UTC),  # 10:00 МСК
        datetime(2026, 9, 28, 7, 15, tzinfo=UTC),
        datetime(2026, 9, 28, 7, 30, tzinfo=UTC),
    ]
    assert page.next_from is None
    assert all(c.v in (150, 100) for c in page.candles)  # 15 и 10 минут по объёму 10


def test_native_daily_candles_and_closed_only() -> None:
    page = source(FakeIss()).history("SBER", "1d", START, END, 10)
    assert [c.t for c in page.candles] == [datetime(2026, 9, 27, 21, tzinfo=UTC)]

    early = IssSource(
        httpx.Client(
            transport=httpx.MockTransport(FakeIss()), base_url="https://iss.test"
        ),
        min_interval=0,
        clock=lambda: datetime(2026, 9, 28, 7, 20, tzinfo=UTC),
        sleep=lambda s: None,
    )
    # на 07:20 UTC закрыта только первая 15-минутная свеча и ещё не закрыт день
    assert len(early.history("SBER", "15m", START, END, 10).candles) == 1
    assert early.history("SBER", "1d", START, END, 10).candles == []


def test_limit_gives_next_from() -> None:
    page = source(FakeIss(minutes=120)).history("SBER", "15m", START, END, 3)

    assert len(page.candles) == 3
    assert page.next_from == datetime(2026, 9, 28, 7, 45, tzinfo=UTC)
    assert page.next_from is not None
    rest = source(FakeIss(minutes=120)).history("SBER", "15m", page.next_from, END, 100)
    assert rest.candles[0].t == page.next_from


def test_page_ceiling_leaves_the_last_bucket_for_the_next_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("trader_importer.iss.MAX_PAGES", 2)
    fake = FakeIss(minutes=PAGE * 2 + 100)

    page = source(fake).history("SBER", "15m", START, END, 10_000)

    # получено 2 страницы по 500 минут: последняя (возможно неполная) свеча не отдана
    assert page.next_from is not None
    assert page.candles[-1].t < page.next_from
    assert len(fake.calls) == 2


def test_unknown_ticker_and_empty_period() -> None:
    fake = FakeIss(known="SBER")
    with pytest.raises(UnknownTicker):
        source(fake).history("NOPE", "1h", START, END, 10)
    assert (
        source(FakeIss(minutes=0)).history("SBER", "1h", START, END, 10).candles == []
    )


def test_upstream_errors_and_retries() -> None:
    fake = FakeIss()
    fake.fail_with = 503
    with pytest.raises(SourceError):
        source(fake).tickers()
    assert len(fake.calls) == 3  # повторные попытки

    fake = FakeIss()
    fake.fail_with = 400
    with pytest.raises(SourceError):
        source(fake).tickers()
    assert len(fake.calls) == 1  # клиентскую ошибку не повторяем
