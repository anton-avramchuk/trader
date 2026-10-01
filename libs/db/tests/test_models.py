from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import configure_mappers

from trader_db.models import Base

EXPECTED_TABLES = {
    "timeframes",
    "instruments",
    "candles",
    "candle_loads",
    "jobs",
    "job_schedules",
    "backtest_experiments",
    "backtest_log",
    "backtest_test_locks",
    "backtest_trades",
    "backtest_windows",
    "chart_profiles",
    "engine_runs",
    "engine_events",
    "manual_fib_grids",
}


def test_mappers_configure_and_tables_registered() -> None:
    configure_mappers()

    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_candle_primary_key_is_instrument_timeframe_open_time() -> None:
    key = Base.metadata.tables["candles"].primary_key
    assert [column.name for column in key.columns] == [
        "instrument_id",
        "timeframe_code",
        "open_time",
    ]


def test_instrument_ticker_is_unique() -> None:
    table = Base.metadata.tables["instruments"]
    unique = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("ticker",) in unique
