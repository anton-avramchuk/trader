"""Интеграционные тесты схемы на временной БД (нужен TRADER_DATABASE_URL)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from alembic import command
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from trader_db import create_instrument, make_engine
from trader_db.migrate import alembic_config
from trader_db.models import Candle, CandleLoad, Instrument, Timeframe

REMOVED_TABLES = {
    "roots",
    "contracts",
    "roll_events",
    "contract_step_prices",
    "raw_candles_1m",
    "derived_candles",
    "dataset_versions",
    "data_imports",
    "import_conflicts",
    "import_presets",
    "data_providers",
    "trading_calendars",
}


def test_models_match_migrations(temp_database_url: str) -> None:
    """Модели и миграции не разошлись (колонки, индексы, внешние ключи)."""
    config = alembic_config(temp_database_url)
    command.upgrade(config, "head")

    command.check(config)


def test_old_model_is_gone_and_new_tables_exist(temp_database_url: str) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "head")
    engine = make_engine(temp_database_url)

    tables = set(inspect(engine).get_table_names())

    assert not tables & REMOVED_TABLES
    assert {"instruments", "candles", "candle_loads", "engine_runs"} <= tables
    engine.dispose()


def test_migration_0016_is_irreversible(temp_database_url: str) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "head")

    with pytest.raises(NotImplementedError):
        command.downgrade(config, "0015")


def test_migration_keeps_global_profiles_and_clears_dependent_rows(
    temp_database_url: str,
) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0015")
    engine = make_engine(temp_database_url)
    with engine.begin() as connection:
        calendar = connection.scalar(
            text("SELECT id FROM trading_calendars WHERE code = 'moex_forts'")
        )
        root = connection.scalar(
            text(
                "INSERT INTO roots (code, name, exchange, quote_currency, tick_size, "
                "calendar_id, roll_trading_days) VALUES ('BR', 'Brent', 'MOEX', 'USD', "
                "0.01, :c, 5) RETURNING id"
            ),
            {"c": calendar},
        )
        connection.execute(
            text(
                "INSERT INTO chart_profiles (name, root_id, config) VALUES "
                "('global', NULL, '{}'), ('scoped', :r, '{}')"
            ),
            {"r": root},
        )
        connection.execute(
            text(
                "INSERT INTO engine_runs (engine, algorithm_version, params, "
                "params_hash, root_id, timeframe_code) VALUES "
                "('zigzag', 1, '{}', 'h', :r, '15m')"
            ),
            {"r": root},
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        names = connection.scalars(text("SELECT name FROM chart_profiles")).all()
        runs = connection.scalar(text("SELECT count(*) FROM engine_runs"))
        codes = connection.scalars(
            text("SELECT code FROM timeframes ORDER BY sort_order")
        ).all()
    assert names == ["global"]
    assert runs == 0
    assert codes == ["15m", "1h", "4h", "1d", "1w"]
    engine.dispose()


def test_reference_timeframes_are_seeded(session: Session) -> None:
    timeframes = session.scalars(select(Timeframe).order_by(Timeframe.sort_order)).all()

    assert [t.code for t in timeframes] == ["15m", "1h", "4h", "1d", "1w"]
    assert not any(t.is_internal for t in timeframes)


def new_instrument(session: Session, ticker: str = "SBER", **kw: object) -> Instrument:
    values: dict[str, object] = {
        "ticker": ticker,
        "name": "Сбербанк",
        "currency": "RUB",
        "tick_size": Decimal("0.01"),
        "timezone": "Europe/Moscow",
        "source": "test",
    }
    return create_instrument(session, **(values | kw))  # type: ignore[arg-type]


def test_ticker_is_unique(session: Session) -> None:
    new_instrument(session)

    with pytest.raises(IntegrityError):
        new_instrument(session)


@pytest.mark.parametrize("tick_size", [Decimal(0), Decimal("-0.01")])
def test_tick_size_must_be_positive(session: Session, tick_size: Decimal) -> None:
    with pytest.raises(IntegrityError):
        new_instrument(session, tick_size=tick_size)


@pytest.mark.parametrize("tick_value", [Decimal(0), Decimal("-1")])
def test_tick_value_is_positive_or_empty(session: Session, tick_value: Decimal) -> None:
    assert new_instrument(session, "OK").tick_value is None
    with pytest.raises(IntegrityError):
        new_instrument(session, "BAD", tick_value=tick_value)


OPENED = datetime(2026, 9, 28, 7, tzinfo=UTC)


def candle(instrument: int, opened: datetime, **kw: object) -> Candle:
    values: dict[str, object] = {
        "instrument_id": instrument,
        "timeframe_code": "1h",
        "open_time": opened,
        "close_time": datetime(2026, 9, 28, 8, tzinfo=UTC),
        "open": Decimal(100),
        "high": Decimal(101),
        "low": Decimal(99),
        "close": Decimal(100),
        "volume": Decimal(5),
        "trading_day": date(2026, 9, 28),
    }
    return Candle(**(values | kw))


def test_candle_key_is_unique(session: Session) -> None:
    instrument = new_instrument(session).id
    session.add(candle(instrument, OPENED))
    session.flush()

    session.add(candle(instrument, OPENED))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


@pytest.mark.parametrize(
    "bad",
    [
        {"high": Decimal(98)},  # high ниже low
        {"close_time": datetime(2026, 9, 28, 7, tzinfo=UTC)},  # закрытие = открытию
        {"timeframe_code": "1m"},  # нет такого таймфрейма
    ],
)
def test_candle_rejects_bad_rows(session: Session, bad: dict[str, object]) -> None:
    instrument = new_instrument(session).id
    session.add(candle(instrument, OPENED, **bad))

    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_deleting_instrument_removes_candles_and_loads(session: Session) -> None:
    instrument = new_instrument(session)
    session.add(candle(instrument.id, OPENED))
    session.add(
        CandleLoad(
            instrument_id=instrument.id,
            timeframe_code="1h",
            period_from=OPENED,
            period_to=datetime(2026, 9, 29, tzinfo=UTC),
            rows=1,
            source="test",
        )
    )
    session.flush()

    session.delete(instrument)
    session.flush()

    assert session.scalars(select(Candle)).all() == []
    assert session.scalars(select(CandleLoad)).all() == []
