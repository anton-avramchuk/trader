"""Задача candles.load и клиент importer на фейковом importer (нужна БД)."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from trader_db import count_candles, read_bars
from trader_db.models import CandleLoad

from tests.conftest import FakeImporter
from tests.helpers import run_job
from trader_worker.candles_jobs import parse_moment
from trader_worker.importer_client import ImporterClient, ImporterError
from trader_worker.runner import Worker

DAY = date(2026, 9, 28)
NEXT_DAY = date(2026, 9, 29)


def load(
    worker: Worker, factory: sessionmaker[Session], instrument: int, **params: Any
) -> Any:
    base: dict[str, Any] = {
        "instrument_id": instrument,
        "period_from": DAY.isoformat(),
        "period_to": NEXT_DAY.isoformat(),
        "timeframes": ["1h"],
    }
    return run_job(worker, factory, "candles.load", **(base | params))


def test_load_writes_all_pages_with_trading_days_and_log(
    loader_worker: Worker,
    session_factory: sessionmaker[Session],
    instrument_id: int,
    importer: FakeImporter,
) -> None:
    job = load(loader_worker, session_factory, instrument_id)

    assert job.status == "succeeded", job.error
    assert job.result["loaded"] == {"1h": 24}  # сутки по МСК = 24 часовые свечи
    assert len(importer.calls) == 8  # по 3 свечи на страницу
    with session_factory() as session:
        bars = read_bars(session, instrument_id, "1h")
        loads = session.scalars(select(CandleLoad)).all()
    assert len(bars) == 24
    # сутки 28.09 по Москве начинаются в 21:00 UTC 27.09
    assert bars[0].timestamp == datetime(2026, 9, 27, 21, tzinfo=UTC)
    assert bars[0].close_time == bars[0].timestamp + timedelta(hours=1)
    assert bars[0].trading_day == DAY and bars[-1].trading_day == DAY
    [log] = loads
    assert log.rows == 24 and log.timeframe_code == "1h" and log.job_id == job.id
    assert log.source == "importer"


def test_reload_overwrites_instead_of_duplicating(
    loader_worker: Worker,
    session_factory: sessionmaker[Session],
    instrument_id: int,
) -> None:
    load(loader_worker, session_factory, instrument_id)
    load(loader_worker, session_factory, instrument_id)

    with session_factory() as session:
        assert count_candles(session, instrument_id, "1h") == 24
        assert session.scalar(select(func.count()).select_from(CandleLoad)) == 2


def test_default_timeframes_and_datetime_period(
    loader_worker: Worker, session_factory: sessionmaker[Session], instrument_id: int
) -> None:
    job = run_job(
        loader_worker,
        session_factory,
        "candles.load",
        instrument_id=instrument_id,
        period_from="2026-09-28T07:00:00Z",
        period_to="2026-09-28T09:00:00Z",
    )

    assert job.status == "succeeded", job.error
    assert set(job.result["loaded"]) == {"15m", "1h", "4h", "1d", "1w"}
    assert job.result["loaded"]["15m"] == 8 and job.result["loaded"]["1h"] == 2


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"instrument_id": None}, "instrument_id"),
        ({"instrument_id": 999_999}, "не найден"),
        ({"timeframes": ["2h"]}, "таймфрейм"),
        ({"period_from": "вчера"}, "дата"),
        ({"period_from": None}, "дата"),
        ({"period_from": "2026-09-30", "period_to": "2026-09-29"}, "раньше"),
    ],
)
def test_bad_parameters_give_readable_errors(
    loader_worker: Worker,
    session_factory: sessionmaker[Session],
    instrument_id: int,
    params: dict[str, Any],
    message: str,
) -> None:
    job = load(loader_worker, session_factory, instrument_id, **params)

    assert job.status == "failed" and message in (job.error or "")


def test_importer_errors_become_job_errors(
    loader_worker: Worker,
    session_factory: sessionmaker[Session],
    instrument_id: int,
    importer: FakeImporter,
) -> None:
    importer.fail_with = 502
    job = load(loader_worker, session_factory, instrument_id)

    assert job.status == "failed" and "502" in (job.error or "")
    assert "источник упал" in (job.error or "")
    with session_factory() as session:
        assert count_candles(session, instrument_id, "1h") == 0


def test_unknown_ticker_in_importer_is_reported(
    loader_worker: Worker,
    session_factory: sessionmaker[Session],
    instrument_id: int,
    importer: FakeImporter,
) -> None:
    importer.known = set()

    job = load(loader_worker, session_factory, instrument_id)

    assert job.status == "failed" and "не найден" in (job.error or "")


def test_parse_moment_variants() -> None:
    from zoneinfo import ZoneInfo

    msk = ZoneInfo("Europe/Moscow")

    assert parse_moment("2026-09-28", msk) == datetime(2026, 9, 27, 21, tzinfo=UTC)
    assert parse_moment("2026-09-28T10:00:00", msk) == datetime(
        2026, 9, 28, 7, tzinfo=UTC
    )
    assert parse_moment("2026-09-28T10:00:00+03:00", msk) == datetime(
        2026, 9, 28, 7, tzinfo=UTC
    )
    assert parse_moment(date(2026, 9, 28), msk) == datetime(2026, 9, 27, 21, tzinfo=UTC)
    assert parse_moment(datetime(2026, 9, 28, 7), msk) == datetime(
        2026, 9, 28, 4, tzinfo=UTC
    )


def client_for(handler: Any) -> ImporterClient:
    return ImporterClient(
        client=httpx.Client(
            transport=httpx.MockTransport(handler), base_url="http://importer.test"
        )
    )


def test_client_pages_follow_next_from_and_stop() -> None:
    fake = FakeImporter()
    client = client_for(fake)
    start = datetime(2026, 9, 28, tzinfo=UTC)

    pages = list(client.pages("SBER", "1h", start, start + timedelta(hours=7)))

    assert [len(p) for p in pages] == [3, 3, 1]
    assert pages[1][0].t == start + timedelta(hours=3)
    assert fake.calls[1].url.params["from"] == "2026-09-28T03:00:00Z"


def test_client_rejects_a_stuck_cursor() -> None:
    def stuck(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ticker": "SBER",
                "tf": "1h",
                "candles": [],
                "next_from": request.url.params["from"],
            },
        )

    start = datetime(2026, 9, 28, tzinfo=UTC)

    with pytest.raises(ImporterError, match="не продвигается"):
        list(client_for(stuck).pages("SBER", "1h", start, start + timedelta(hours=5)))


def test_client_tickers_and_connection_errors() -> None:
    client = client_for(FakeImporter())
    assert [t["ticker"] for t in client.tickers()] == ["SBER"]
    assert client.ticker("SBER")["tick_size"] == "0.01"
    with pytest.raises(ImporterError, match="не найден"):
        client.ticker("NOPE")

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ImporterError, match="недоступен"):
        client_for(down).tickers()
