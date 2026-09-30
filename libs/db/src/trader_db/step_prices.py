"""Дневная стоимость шага цены контракта (``contract_step_prices``, ADR-0007)."""

from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from trader_db.models import ContractStepPrice


def upsert_step_prices(
    session: Session, contract_id: int, prices: Iterable[tuple[date, Decimal]]
) -> int:
    """Записать (или обновить) стоимость шага по дням; вернуть число дней."""
    rows = [
        {"contract_id": contract_id, "date": day, "step_price": price}
        for day, price in prices
    ]
    if not rows:
        return 0
    statement = insert(ContractStepPrice).values(rows)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=["contract_id", "date"],
            set_={"step_price": statement.excluded.step_price},
        )
    )
    return len(rows)


def last_step_price_date(session: Session, contract_id: int) -> date | None:
    return session.scalar(
        select(func.max(ContractStepPrice.date)).where(
            ContractStepPrice.contract_id == contract_id
        )
    )


def list_step_prices(
    session: Session,
    contract_id: int,
    *,
    start: date | None = None,
    end: date | None = None,
    limit: int | None = None,
    newest_first: bool = False,
) -> list[ContractStepPrice]:
    """Стоимость шага по дням; ``[start, end]`` включительно."""
    query = select(ContractStepPrice).where(
        ContractStepPrice.contract_id == contract_id
    )
    if start is not None:
        query = query.where(ContractStepPrice.date >= start)
    if end is not None:
        query = query.where(ContractStepPrice.date <= end)
    query = query.order_by(
        ContractStepPrice.date.desc() if newest_first else ContractStepPrice.date
    )
    if limit is not None:
        query = query.limit(limit)
    return list(session.scalars(query))


def step_price_on(session: Session, contract_id: int, day: date) -> Decimal | None:
    """Стоимость шага на день: последняя известная не позже ``day`` (без будущего)."""
    return session.scalar(
        select(ContractStepPrice.step_price)
        .where(
            ContractStepPrice.contract_id == contract_id,
            ContractStepPrice.date <= day,
        )
        .order_by(ContractStepPrice.date.desc())
        .limit(1)
    )
