"""Continuous-серия в БД: события ролла и склеенные бары (нужен TRADER_DATABASE_URL)."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from trader_engine.aggregation import TIMEFRAMES
from trader_engine.ingest import Candle1m

from tests.test_derived import CALENDAR, build_all, candles_of, load
from trader_db import (
    load_rolls,
    load_segments,
    read_bars,
    read_continuous,
    update_rolls,
)
from trader_db.models import Contract, Root

# Ролл контракта A (экспирация 15.10.2026) при N = 5 — неделя с 05.10.
A_EXPIRATION, B_EXPIRATION = date(2026, 10, 15), date(2026, 11, 12)
BEFORE = [date(2026, 9, 28), date(2026, 9, 29)]
AFTER = [date(2026, 10, 5), date(2026, 10, 6)]
ROLL_AT = CALENDAR.trading_week_bounds(date(2026, 10, 5))
FACTOR = Decimal(2)


def scaled(candles: list[Candle1m], factor: Decimal) -> list[Candle1m]:
    return [
        replace(
            c,
            open=c.open * factor,
            high=c.high * factor,
            low=c.low * factor,
            close=c.close * factor,
        )
        for c in candles
    ]


@pytest.fixture
def two_contracts(session: Session, contract_id: int) -> tuple[int, int, int]:
    """Root, контракт A (данные до и после ролла) и B (те же дни, цены ×2)."""
    first = session.get(Contract, contract_id)
    assert first is not None
    first.expiration_date = A_EXPIRATION
    second = Contract(root_id=first.root_id, expiration_date=B_EXPIRATION)
    session.add(second)
    session.flush()
    days = candles_of(BEFORE + AFTER)
    for contract, candles in ((first.id, days), (second.id, scaled(days, FACTOR))):
        load(session, contract, candles)
        build_all(session, contract)
    return first.root_id, first.id, second.id


def test_roll_event_ratio_and_moment(
    session: Session, two_contracts: tuple[int, int, int]
) -> None:
    root_id, a, b = two_contracts

    events = update_rolls(session, root_id, CALENDAR)

    assert len(events) == 1
    event = events[0]
    assert (event.from_contract_id, event.to_contract_id) == (a, b)
    assert ROLL_AT is not None and event.rolled_at == ROLL_AT[0]
    assert event.available_at == event.rolled_at
    assert event.ratio == FACTOR
    assert event.basis_trading_day == BEFORE[-1]


def test_update_is_idempotent_and_deterministic(
    session: Session, two_contracts: tuple[int, int, int]
) -> None:
    root_id, *_ = two_contracts
    update_rolls(session, root_id, CALENDAR)
    first = [(e.id, e.rolled_at, e.ratio) for e in load_rolls(session, root_id)]

    update_rolls(session, root_id, CALENDAR)
    second = [(e.id, e.rolled_at, e.ratio) for e in load_rolls(session, root_id)]

    assert first == second


@pytest.mark.parametrize("timeframe", list(TIMEFRAMES))
def test_no_bar_crosses_the_roll_and_prices_are_rescaled(
    session: Session, two_contracts: tuple[int, int, int], timeframe: str
) -> None:
    root_id, a, b = two_contracts
    update_rolls(session, root_id, CALENDAR)
    assert ROLL_AT is not None
    roll = ROLL_AT[0]

    bars = read_continuous(session, root_id, timeframe)

    assert bars
    for item in bars:
        bar = item.bar
        if bar.timestamp < roll:
            assert item.contract_id == a and bar.close_time <= roll
            raw = next(
                r
                for r in read_bars(session, a, timeframe)
                if r.timestamp == bar.timestamp
            )
            assert bar.close == raw.close * FACTOR
        else:
            assert item.contract_id == b and item.factor == 1
    stamps = [item.bar.timestamp for item in bars]
    assert stamps == sorted(set(stamps))


def test_returns_inside_segments_match_the_source_contracts(
    session: Session, two_contracts: tuple[int, int, int]
) -> None:
    root_id, a, _ = two_contracts
    update_rolls(session, root_id, CALENDAR)
    assert ROLL_AT is not None

    inside = [
        item.bar
        for item in read_continuous(session, root_id, "15m")
        if item.contract_id == a
    ]
    original = read_bars(session, a, "15m", end=ROLL_AT[0])

    assert len(inside) == len(original) > 2
    for (previous, current), (raw_previous, raw_current) in zip(
        zip(inside, inside[1:], strict=False),
        zip(original, original[1:], strict=False),
        strict=False,
    ):
        assert abs(
            current.close / previous.close - raw_current.close / raw_previous.close
        ) < Decimal("1e-15")


def test_range_filter_clips_to_the_requested_interval(
    session: Session, two_contracts: tuple[int, int, int]
) -> None:
    root_id, *_ = two_contracts
    update_rolls(session, root_id, CALENDAR)
    assert ROLL_AT is not None
    full = read_continuous(session, root_id, "1d")

    tail = read_continuous(session, root_id, "1d", start=ROLL_AT[0])

    assert tail == [item for item in full if item.bar.timestamp >= ROLL_AT[0]]
    assert 0 < len(tail) < len(full)


def test_pending_roll_when_next_contract_has_no_common_day(
    session: Session, contract_id: int
) -> None:
    first = session.get(Contract, contract_id)
    assert first is not None
    first.expiration_date = A_EXPIRATION
    second = Contract(root_id=first.root_id, expiration_date=B_EXPIRATION)
    session.add(second)
    session.flush()
    load(session, first.id, candles_of(BEFORE + AFTER))
    load(session, second.id, candles_of(AFTER))  # у B нет дней до ролла
    build_all(session, first.id)
    build_all(session, second.id)

    events = update_rolls(session, first.root_id, CALENDAR)

    assert events == []
    segments = load_segments(session, first.root_id)
    assert [(s.contract_id, s.start, s.end) for s in segments] == [
        (first.id, None, None)
    ]


def test_changed_roll_days_drop_the_stale_event(
    session: Session, two_contracts: tuple[int, int, int]
) -> None:
    root_id, *_ = two_contracts
    update_rolls(session, root_id, CALENDAR)
    root = session.scalars(select(Root).where(Root.id == root_id)).one()
    root.roll_trading_days = 10  # ролл уходит на 21.09, где у контрактов нет данных

    update_rolls(session, root_id, CALENDAR)

    assert load_rolls(session, root_id) == []


def test_unknown_root_is_an_error(session: Session) -> None:
    with pytest.raises(LookupError):
        update_rolls(session, 999_999, CALENDAR)
