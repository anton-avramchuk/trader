"""Инструменты и свечи: запись, перезапись, чтение срезами (нужна БД)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.conftest import make_instrument
from trader_db import (
    CandleRow,
    count_candles,
    create_instrument,
    find_instrument,
    get_instrument,
    last_open_time,
    list_instruments,
    read_bars,
    record_load,
    upsert_candles,
)

T0 = datetime(2026, 9, 28, 7, tzinfo=UTC)


def row(i: int, close: str = "100", **kw: object) -> CandleRow:
    opened = T0 + timedelta(hours=i)
    values: dict[str, object] = {
        "open_time": opened,
        "close_time": opened + timedelta(hours=1),
        "open": Decimal("100"),
        "high": Decimal("102"),
        "low": Decimal("99"),
        "close": Decimal(close),
        "volume": Decimal("10"),
        "trading_day": date(2026, 9, 28),
    }
    return CandleRow(**(values | kw))  # type: ignore[arg-type]


def test_instruments_lookup_and_listing(session: Session) -> None:
    sber = make_instrument(session, "SBER")
    gazp = make_instrument(session, "GAZP")

    assert [i.ticker for i in list_instruments(session)] == ["GAZP", "SBER"]
    found = find_instrument(session, "SBER")
    assert found is not None and found.id == sber
    assert find_instrument(session, "NOPE") is None
    fetched = get_instrument(session, gazp)
    assert fetched is not None and fetched.timezone == "Europe/Moscow"
    with pytest.raises(IntegrityError):
        create_instrument(
            session,
            ticker="SBER",
            name="x",
            currency="RUB",
            tick_size=Decimal("0.01"),
            timezone="UTC",
            source="t",
        )
    session.rollback()


def test_upsert_then_read_in_order(session: Session, instrument_id: int) -> None:
    written = upsert_candles(session, instrument_id, "1h", [row(2), row(0), row(1)])

    bars = read_bars(session, instrument_id, "1h")

    assert written == 3
    assert [b.timestamp for b in bars] == [
        T0,
        T0 + timedelta(hours=1),
        T0 + timedelta(hours=2),
    ]
    first = bars[0]
    assert first.timeframe == "1h" and first.close == Decimal("100")
    assert first.close_time == T0 + timedelta(hours=1)
    assert first.trading_day == date(2026, 9, 28)
    assert count_candles(session, instrument_id, "1h") == 3
    assert count_candles(session, instrument_id, "15m") == 0


def test_upsert_replaces_existing_candles(session: Session, instrument_id: int) -> None:
    upsert_candles(session, instrument_id, "1h", [row(0, "100"), row(1, "101")])

    upsert_candles(session, instrument_id, "1h", [row(1, "150"), row(2, "102")])

    bars = read_bars(session, instrument_id, "1h")
    assert [str(b.close) for b in bars] == [
        "100.00000000",
        "150.00000000",
        "102.00000000",
    ]
    assert count_candles(session, instrument_id, "1h") == 3


def test_large_upsert_is_chunked(session: Session, instrument_id: int) -> None:
    rows = [row(i) for i in range(4500)]

    assert upsert_candles(session, instrument_id, "1h", rows) == 4500
    assert count_candles(session, instrument_id, "1h") == 4500
    assert upsert_candles(session, instrument_id, "1h", []) == 0


def test_range_closed_until_limit_and_tail(
    session: Session, instrument_id: int
) -> None:
    upsert_candles(session, instrument_id, "1h", [row(i) for i in range(10)])

    window = read_bars(
        session, instrument_id, "1h", T0 + timedelta(hours=2), T0 + timedelta(hours=5)
    )
    assert [b.timestamp for b in window] == [T0 + timedelta(hours=h) for h in (2, 3, 4)]

    known = read_bars(
        session, instrument_id, "1h", closed_until=T0 + timedelta(hours=4)
    )
    assert len(known) == 4 and known[-1].close_time == T0 + timedelta(hours=4)

    head = read_bars(session, instrument_id, "1h", limit=2)
    tail = read_bars(session, instrument_id, "1h", limit=2, tail=True)
    assert [b.timestamp for b in head] == [T0, T0 + timedelta(hours=1)]
    assert [b.timestamp for b in tail] == [
        T0 + timedelta(hours=8),
        T0 + timedelta(hours=9),
    ]


def test_series_are_isolated_by_instrument_and_timeframe(session: Session) -> None:
    sber, gazp = make_instrument(session, "SBER"), make_instrument(session, "GAZP")
    upsert_candles(session, sber, "1h", [row(0)])
    upsert_candles(session, gazp, "1h", [row(0), row(1)])
    upsert_candles(session, sber, "4h", [row(0, close_time=T0 + timedelta(hours=4))])

    assert len(read_bars(session, sber, "1h")) == 1
    assert len(read_bars(session, gazp, "1h")) == 2
    assert len(read_bars(session, sber, "4h")) == 1
    assert read_bars(session, gazp, "4h") == []


def test_last_open_time_and_load_log(session: Session, instrument_id: int) -> None:
    assert last_open_time(session, instrument_id, "1h") is None
    upsert_candles(session, instrument_id, "1h", [row(0), row(5)])

    assert last_open_time(session, instrument_id, "1h") == T0 + timedelta(hours=5)
    load = record_load(
        session,
        instrument_id=instrument_id,
        timeframe="1h",
        period_from=T0,
        period_to=T0 + timedelta(days=1),
        rows=2,
        source="importer",
    )
    assert load.id is not None and load.rows == 2 and load.job_id is None


def test_bad_candle_is_rejected_by_the_database(
    session: Session, instrument_id: int
) -> None:
    with pytest.raises(IntegrityError):
        upsert_candles(
            session,
            instrument_id,
            "1h",
            [row(0, high=Decimal("90"), low=Decimal("99"))],
        )
    session.rollback()
