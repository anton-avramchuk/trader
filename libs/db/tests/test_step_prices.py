"""Дневная стоимость шага цены (нужен TRADER_DATABASE_URL)."""

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from trader_db import (
    last_step_price_date,
    list_step_prices,
    step_price_on,
    upsert_step_prices,
)

D = Decimal


def test_upsert_inserts_then_updates_the_same_day(
    session: Session, contract_id: int
) -> None:
    assert last_step_price_date(session, contract_id) is None

    upsert_step_prices(
        session,
        contract_id,
        [(date(2026, 3, 10), D("7.87")), (date(2026, 3, 11), D("7.9"))],
    )
    updated = upsert_step_prices(
        session,
        contract_id,
        [(date(2026, 3, 11), D("8.01")), (date(2026, 3, 12), D("8.1"))],
    )

    rows = list_step_prices(session, contract_id)
    assert updated == 2
    assert [(r.date, r.step_price) for r in rows] == [
        (date(2026, 3, 10), D("7.87")),
        (date(2026, 3, 11), D("8.01")),
        (date(2026, 3, 12), D("8.1")),
    ]
    assert last_step_price_date(session, contract_id) == date(2026, 3, 12)


def test_empty_input_is_a_noop(session: Session, contract_id: int) -> None:
    assert upsert_step_prices(session, contract_id, []) == 0


def test_range_limit_and_order(session: Session, contract_id: int) -> None:
    upsert_step_prices(
        session,
        contract_id,
        [(date(2026, 3, day), D(day)) for day in range(9, 14)],
    )

    window = list_step_prices(
        session, contract_id, start=date(2026, 3, 10), end=date(2026, 3, 12)
    )
    newest = list_step_prices(session, contract_id, limit=2, newest_first=True)

    assert [r.date.day for r in window] == [10, 11, 12]
    assert [r.date.day for r in newest] == [13, 12]


def test_price_on_a_day_uses_the_last_known_and_never_looks_ahead(
    session: Session, contract_id: int
) -> None:
    upsert_step_prices(
        session,
        contract_id,
        [(date(2026, 3, 10), D("7.87")), (date(2026, 3, 13), D("8.00"))],
    )

    assert step_price_on(session, contract_id, date(2026, 3, 9)) is None
    assert step_price_on(session, contract_id, date(2026, 3, 12)) == D("7.87")
    assert step_price_on(session, contract_id, date(2026, 3, 13)) == D("8.00")
    assert step_price_on(session, contract_id, date(2026, 4, 1)) == D("8.00")
