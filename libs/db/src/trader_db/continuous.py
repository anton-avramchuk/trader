"""Continuous-серия root: события ролла и чтение склеенных баров (ADR-0019).

Склейка не материализуется: бары контрактов лежат в ``derived_candles``, а
``roll_events`` задают, какой контракт когда фронтовый, и ratio. Бары continuous
собираются при чтении (``read_continuous``); поэтому новый ролл не переписывает
историю, а меняет только коэффициенты сегментов.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import delete, distinct, select
from sqlalchemy.orm import Session
from trader_engine.calendar import TradingCalendar
from trader_engine.continuous import (
    RATIO_LOOKBACK_DAYS,
    ContinuousBar,
    ContractSpec,
    Roll,
    Segment,
    adjust_bar,
    build_segments,
    pick_ratio_basis,
    roll_schedule,
)

from trader_db.derived import read_bars
from trader_db.models import Contract, DerivedCandle, RollEvent, Root

DAILY = "1d"


def _expiration(contract: Contract) -> date:
    return contract.last_trade_date or contract.expiration_date


def participating_contracts(session: Session, root_id: int) -> list[Contract]:
    """Контракты root с дневными барами, по возрастанию экспирации."""
    with_data = select(distinct(DerivedCandle.contract_id)).where(
        DerivedCandle.timeframe_code == DAILY
    )
    contracts = session.scalars(
        select(Contract).where(Contract.root_id == root_id, Contract.id.in_(with_data))
    ).all()
    return sorted(contracts, key=lambda contract: (_expiration(contract), contract.id))


def _daily_closes(
    session: Session, contract_id: int, before: datetime
) -> dict[date, Decimal]:
    """Закрытия последних дней контракта, завершившихся до ``before``."""
    rows = session.execute(
        select(DerivedCandle.trading_day, DerivedCandle.close)
        .where(
            DerivedCandle.contract_id == contract_id,
            DerivedCandle.timeframe_code == DAILY,
            DerivedCandle.close_time <= before,
        )
        .order_by(DerivedCandle.timestamp.desc())
        .limit(RATIO_LOOKBACK_DAYS)
    ).all()
    return {day: close for day, close in rows}


def update_rolls(
    session: Session, root_id: int, calendar: TradingCalendar
) -> list[RollEvent]:
    """Пересчитать события ролла root по текущим дневным барам.

    Расписание строится по контрактам, у которых есть данные. Ролл применяется,
    если у обоих контрактов есть общий торговый день до ролла; первый ролл без
    данных и все следующие ждут (серия остаётся на прежнем контракте). События,
    которых больше нет в расписании (сменился N, появился контракт), удаляются.
    Транзакцию фиксирует вызывающий.
    """
    root = session.get(Root, root_id)
    if root is None:
        raise LookupError(f"Root {root_id} не найден")
    contracts = participating_contracts(session, root_id)
    rolls = roll_schedule(
        [ContractSpec(c.id, _expiration(c)) for c in contracts],
        calendar,
        root.roll_trading_days,
    )

    computed: list[tuple[Roll, Decimal, date, Decimal, Decimal]] = []
    for roll in rolls:
        basis = pick_ratio_basis(
            _daily_closes(session, roll.from_contract_id, roll.at),
            _daily_closes(session, roll.to_contract_id, roll.at),
        )
        if basis is None:
            break
        computed.append(
            (roll, basis.ratio, basis.trading_day, basis.from_close, basis.to_close)
        )

    existing = {
        (event.from_contract_id, event.to_contract_id): event
        for event in session.scalars(
            select(RollEvent).where(RollEvent.root_id == root_id)
        )
    }
    keep = {(roll.from_contract_id, roll.to_contract_id) for roll, *_ in computed}
    stale = [event.id for pair, event in existing.items() if pair not in keep]
    if stale:
        session.execute(delete(RollEvent).where(RollEvent.id.in_(stale)))
        session.flush()

    events: list[RollEvent] = []
    for roll, ratio, day, from_close, to_close in computed:
        event = existing.get((roll.from_contract_id, roll.to_contract_id))
        if event is None:
            event = RollEvent(
                root_id=root_id,
                from_contract_id=roll.from_contract_id,
                to_contract_id=roll.to_contract_id,
            )
            session.add(event)
        event.rolled_at = roll.at
        event.ratio = ratio
        event.basis_trading_day = day
        event.from_close = from_close
        event.to_close = to_close
        event.available_at = roll.at
        events.append(event)
    session.flush()
    return events


def load_rolls(session: Session, root_id: int) -> list[RollEvent]:
    return list(
        session.scalars(
            select(RollEvent)
            .where(RollEvent.root_id == root_id)
            .order_by(RollEvent.rolled_at)
        )
    )


def load_segments(session: Session, root_id: int) -> list[Segment]:
    """Сегменты continuous-серии по сохранённым событиям ролла."""
    order = [c.id for c in participating_contracts(session, root_id)]
    applied = [
        (
            Roll(
                event.from_contract_id,
                event.to_contract_id,
                event.rolled_at.date(),
                event.rolled_at,
            ),
            event.ratio,
        )
        for event in load_rolls(session, root_id)
    ]
    return build_segments(order, applied)


def read_continuous(
    session: Session,
    root_id: int,
    timeframe: str,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[ContinuousBar]:
    """Бары continuous-серии ``[start, end)`` в масштабе текущего контракта."""
    result: list[ContinuousBar] = []
    for segment in load_segments(session, root_id):
        first = max((m for m in (start, segment.start) if m is not None), default=None)
        last = min((m for m in (end, segment.end) if m is not None), default=None)
        if first is not None and last is not None and first >= last:
            continue
        result.extend(
            adjust_bar(bar, segment)
            for bar in read_bars(session, segment.contract_id, timeframe, first, last)
        )
    return result
