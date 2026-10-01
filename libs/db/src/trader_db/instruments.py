"""Инструменты (ADR-0028): создание, поиск, список."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from trader_db.models import Instrument


def create_instrument(
    session: Session,
    *,
    ticker: str,
    name: str,
    currency: str,
    tick_size: Decimal,
    timezone: str,
    source: str,
    tick_value: Decimal | None = None,
) -> Instrument:
    """Новый инструмент; тикер уникален (``IntegrityError`` при повторе)."""
    instrument = Instrument(
        ticker=ticker,
        name=name,
        currency=currency,
        tick_size=tick_size,
        timezone=timezone,
        source=source,
        tick_value=tick_value,
    )
    session.add(instrument)
    session.flush()
    return instrument


def get_instrument(session: Session, instrument_id: int) -> Instrument | None:
    return session.get(Instrument, instrument_id)


def find_instrument(session: Session, ticker: str) -> Instrument | None:
    return session.scalars(
        select(Instrument).where(Instrument.ticker == ticker)
    ).one_or_none()


def list_instruments(session: Session) -> list[Instrument]:
    return list(session.scalars(select(Instrument).order_by(Instrument.ticker)))
