"""Интеграционные тесты схемы на временной БД (нужен TRADER_DATABASE_URL)."""

from datetime import date
from decimal import Decimal

import pytest
from alembic import command
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from trader_db import make_engine
from trader_db.migrate import alembic_config
from trader_db.models import (
    Contract,
    ContractProviderId,
    ContractStepPrice,
    DataProvider,
    Root,
    Timeframe,
    TradingCalendar,
)


def make_root(session: Session, code: str = "BR") -> Root:
    calendar = TradingCalendar(code=f"cal_{code}", name="MOEX FORTS")
    session.add(calendar)
    session.flush()
    root = Root(
        code=code,
        name="Brent",
        exchange="MOEX",
        quote_currency="USD",
        tick_size=Decimal("0.01"),
        calendar_id=calendar.id,
        roll_trading_days=5,
    )
    session.add(root)
    session.flush()
    return root


def test_migrations_apply_and_roll_back(temp_database_url: str) -> None:
    config = alembic_config(temp_database_url)
    engine = make_engine(temp_database_url)

    command.upgrade(config, "head")
    assert "contracts" in inspect(engine).get_table_names()

    command.downgrade(config, "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    with engine.connect() as connection:
        leftover = connection.scalars(
            text(
                "SELECT proname FROM pg_proc "
                "WHERE proname IN ('raw_candles_1m_guard', 'forbid_modification')"
            )
        ).all()
    assert leftover == []

    command.upgrade(config, "head")
    assert "contracts" in inspect(engine).get_table_names()
    engine.dispose()


def test_models_match_migrations(temp_database_url: str) -> None:
    """Модели и миграции не разошлись (колонки, индексы, внешние ключи)."""
    config = alembic_config(temp_database_url)
    command.upgrade(config, "head")

    command.check(config)


def test_reference_data_is_seeded(session: Session) -> None:
    timeframes = session.scalars(select(Timeframe).order_by(Timeframe.sort_order)).all()
    providers = session.scalars(select(DataProvider.code)).all()

    assert [t.code for t in timeframes] == ["1m", "15m", "1h", "4h", "1d", "1w"]
    assert [t.code for t in timeframes if t.is_internal] == ["1m"]
    assert set(providers) == {"moex_iss", "csv"}


def test_root_defaults(session: Session) -> None:
    root = make_root(session)
    session.refresh(root)

    assert root.include_weekend_sessions is False
    assert root.calendar.timezone == "Europe/Moscow"


def test_contract_is_unique_per_root_and_expiration(session: Session) -> None:
    root = make_root(session)
    session.add(Contract(root_id=root.id, expiration_date=date(2026, 12, 1)))
    session.flush()

    session.add(Contract(root_id=root.id, expiration_date=date(2026, 12, 1)))
    with pytest.raises(IntegrityError):
        session.flush()


def test_secid_may_repeat_across_decades(session: Session) -> None:
    root = make_root(session)
    session.add_all(
        [
            Contract(root_id=root.id, expiration_date=date(2016, 12, 1), secid="BRZ6"),
            Contract(root_id=root.id, expiration_date=date(2026, 12, 1), secid="BRZ6"),
        ]
    )

    session.flush()


def test_last_trade_date_cannot_follow_expiration(session: Session) -> None:
    root = make_root(session)
    session.add(
        Contract(
            root_id=root.id,
            expiration_date=date(2026, 12, 1),
            last_trade_date=date(2026, 12, 2),
        )
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_provider_ids_and_step_prices_are_removed_with_contract(
    session: Session,
) -> None:
    root = make_root(session)
    contract = Contract(root_id=root.id, expiration_date=date(2026, 12, 1))
    session.add(contract)
    session.flush()
    provider = session.scalars(
        select(DataProvider).where(DataProvider.code == "moex_iss")
    ).one()
    session.add_all(
        [
            ContractProviderId(
                contract_id=contract.id,
                provider_id=provider.id,
                id_type="secid",
                external_id="BRZ6",
            ),
            ContractStepPrice(
                contract_id=contract.id,
                date=date(2026, 9, 29),
                step_price=Decimal("8.44"),
            ),
        ]
    )
    session.flush()

    session.delete(contract)
    session.flush()

    assert session.scalars(select(ContractProviderId)).all() == []
    assert session.scalars(select(ContractStepPrice)).all() == []


def test_step_price_is_unique_per_contract_and_date(session: Session) -> None:
    root = make_root(session)
    contract = Contract(root_id=root.id, expiration_date=date(2026, 12, 1))
    session.add(contract)
    session.flush()
    for _ in range(2):
        session.add(
            ContractStepPrice(
                contract_id=contract.id, date=date(2026, 9, 29), step_price=Decimal(8)
            )
        )

    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize("tick_size", [Decimal(0), Decimal("-0.01")])
def test_tick_size_must_be_positive(session: Session, tick_size: Decimal) -> None:
    calendar = TradingCalendar(code="c", name="c")
    session.add(calendar)
    session.flush()
    session.add(
        Root(
            code="X",
            name="x",
            exchange="MOEX",
            quote_currency="USD",
            tick_size=tick_size,
            calendar_id=calendar.id,
            roll_trading_days=1,
        )
    )

    with pytest.raises(IntegrityError):
        session.flush()
