"""abstract instruments and candles

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-01 18:58:52.492916
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Необратимая миграция (ADR-0028): контрактная модель удаляется вместе с данными.
    # Строки, зависящие от старых рядов, больше не имеют смысла; журнал событий
    # неизменяем триггером на UPDATE/DELETE, поэтому очищаем его через TRUNCATE.
    op.execute(
        "TRUNCATE engine_events, engine_runs, manual_fib_grids, backtest_trades, "
        "backtest_windows, backtest_test_locks, backtest_log, backtest_experiments "
        "RESTART IDENTITY"
    )
    op.execute("DELETE FROM chart_profiles WHERE root_id IS NOT NULL")
    op.execute(
        "DELETE FROM job_schedules WHERE job_type IN ('aggregate.contract', "
        "'import.iss', 'import.file', 'iss.sync_root', 'iss.step_prices', "
        "'iss.step_prices_all', 'verify.indicators')"
    )
    op.execute(
        "DELETE FROM jobs WHERE type IN ('aggregate.contract', 'import.iss', "
        "'import.file', 'iss.sync_root', 'iss.step_prices', "
        "'iss.step_prices_all', 'verify.indicators') "
        "AND status IN ('queued', 'running')"
    )
    op.create_table(
        "instruments",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("ticker", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("tick_size", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("tick_value", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "tick_size > 0", name=op.f("ck_instruments_tick_size_positive")
        ),
        sa.CheckConstraint(
            "tick_value IS NULL OR tick_value > 0",
            name=op.f("ck_instruments_tick_value_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instruments")),
        sa.UniqueConstraint("ticker", name=op.f("uq_instruments_ticker")),
    )
    op.create_table(
        "candles",
        sa.Column("instrument_id", sa.BigInteger(), nullable=False),
        sa.Column("timeframe_code", sa.String(length=8), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("close_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("high", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("low", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("close", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("volume", sa.Numeric(precision=24, scale=4), nullable=False),
        sa.Column("trading_day", sa.Date(), nullable=False),
        sa.CheckConstraint(
            "close_time > open_time", name=op.f("ck_candles_close_after_open")
        ),
        sa.CheckConstraint("high >= low", name=op.f("ck_candles_high_not_below_low")),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name=op.f("fk_candles_instrument_id_instruments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["timeframe_code"],
            ["timeframes.code"],
            name=op.f("fk_candles_timeframe_code_timeframes"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "instrument_id", "timeframe_code", "open_time", name="pk_candles"
        ),
    )
    op.create_index(
        "ix_candles_close",
        "candles",
        ["instrument_id", "timeframe_code", "close_time"],
        unique=False,
    )
    op.create_table(
        "candle_loads",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("instrument_id", sa.BigInteger(), nullable=False),
        sa.Column("timeframe_code", sa.String(length=8), nullable=False),
        sa.Column("period_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rows", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "period_from < period_to", name=op.f("ck_candle_loads_period_order")
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name=op.f("fk_candle_loads_instrument_id_instruments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_candle_loads_job_id_jobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["timeframe_code"],
            ["timeframes.code"],
            name=op.f("fk_candle_loads_timeframe_code_timeframes"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_candle_loads")),
    )
    op.create_index(
        "ix_candle_loads_instrument",
        "candle_loads",
        ["instrument_id", "timeframe_code"],
        unique=False,
    )
    op.add_column(
        "backtest_experiments",
        sa.Column("instrument_id", sa.BigInteger(), nullable=False),
    )
    op.add_column(
        "backtest_experiments",
        sa.Column("quantity", sa.Integer(), server_default="1", nullable=False),
    )
    op.drop_index(
        op.f("ix_backtest_experiments_series"), table_name="backtest_experiments"
    )
    op.create_index(
        "ix_backtest_experiments_series",
        "backtest_experiments",
        ["instrument_id", "timeframe_code"],
        unique=False,
    )
    op.drop_constraint(
        op.f("fk_backtest_experiments_root_id_roots"),
        "backtest_experiments",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_backtest_experiments_dataset_version_id_dataset_versions"),
        "backtest_experiments",
        type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk_backtest_experiments_instrument_id_instruments"),
        "backtest_experiments",
        "instruments",
        ["instrument_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("backtest_experiments", "contracts")
    op.drop_column("backtest_experiments", "root_id")
    op.drop_column("backtest_experiments", "dataset_version_id")
    op.add_column(
        "backtest_log", sa.Column("instrument_id", sa.BigInteger(), nullable=True)
    )
    op.drop_index(op.f("ix_backtest_log_series"), table_name="backtest_log")
    op.create_index(
        "ix_backtest_log_series",
        "backtest_log",
        ["instrument_id", "timeframe_code", "family"],
        unique=False,
    )
    op.drop_constraint(
        op.f("fk_backtest_log_root_id_roots"), "backtest_log", type_="foreignkey"
    )
    op.create_foreign_key(
        op.f("fk_backtest_log_instrument_id_instruments"),
        "backtest_log",
        "instruments",
        ["instrument_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_column("backtest_log", "root_id")
    op.add_column(
        "backtest_test_locks",
        sa.Column("instrument_id", sa.BigInteger(), nullable=False),
    )
    op.drop_constraint(
        op.f("uq_backtest_test_locks_root_id"), "backtest_test_locks", type_="unique"
    )
    op.create_unique_constraint(
        op.f("uq_backtest_test_locks_instrument_id"),
        "backtest_test_locks",
        ["instrument_id", "timeframe_code", "family"],
    )
    op.drop_constraint(
        op.f("fk_backtest_test_locks_root_id_roots"),
        "backtest_test_locks",
        type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk_backtest_test_locks_instrument_id_instruments"),
        "backtest_test_locks",
        "instruments",
        ["instrument_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("backtest_test_locks", "root_id")
    op.add_column(
        "backtest_trades", sa.Column("quantity", sa.Integer(), nullable=False)
    )
    op.add_column(
        "backtest_trades", sa.Column("entry_price", sa.Float(), nullable=False)
    )
    op.add_column(
        "backtest_trades", sa.Column("exit_price", sa.Float(), nullable=False)
    )
    op.add_column(
        "backtest_trades", sa.Column("commission", sa.Float(), nullable=False)
    )
    op.add_column(
        "backtest_trades", sa.Column("gross_money", sa.Float(), nullable=True)
    )
    op.add_column("backtest_trades", sa.Column("net_money", sa.Float(), nullable=True))
    op.drop_column("backtest_trades", "rolled")
    op.drop_column("backtest_trades", "commission_rub")
    op.drop_column("backtest_trades", "legs")
    op.drop_column("backtest_trades", "gross_rub")
    op.drop_column("backtest_trades", "step_price_estimated")
    op.drop_column("backtest_trades", "contracts")
    op.drop_column("backtest_trades", "net_rub")
    op.add_column(
        "chart_profiles", sa.Column("instrument_id", sa.BigInteger(), nullable=True)
    )
    op.drop_index(
        op.f("uq_chart_profiles_root_name"),
        table_name="chart_profiles",
        postgresql_where="(root_id IS NOT NULL)",
    )
    op.create_index(
        "uq_chart_profiles_instrument_name",
        "chart_profiles",
        ["instrument_id", "name"],
        unique=True,
        postgresql_where=sa.text("instrument_id IS NOT NULL"),
    )
    op.drop_constraint(
        op.f("fk_chart_profiles_root_id_roots"), "chart_profiles", type_="foreignkey"
    )
    op.create_foreign_key(
        op.f("fk_chart_profiles_instrument_id_instruments"),
        "chart_profiles",
        "instruments",
        ["instrument_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("chart_profiles", "root_id")
    # индекс по имени глобальных профилей зависел от root_id и удалён вместе с колонкой
    op.create_index(
        "uq_chart_profiles_global_name",
        "chart_profiles",
        ["name"],
        unique=True,
        postgresql_where=sa.text("instrument_id IS NULL"),
    )
    op.drop_constraint(
        op.f("fk_engine_events_dataset_version_id_dataset_versions"),
        "engine_events",
        type_="foreignkey",
    )
    op.drop_column("engine_events", "dataset_version_id")
    op.add_column(
        "engine_runs", sa.Column("instrument_id", sa.BigInteger(), nullable=False)
    )
    op.drop_constraint(
        op.f("fk_engine_runs_contract_id_contracts"), "engine_runs", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_engine_runs_dataset_version_id_dataset_versions"),
        "engine_runs",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_engine_runs_root_id_roots"), "engine_runs", type_="foreignkey"
    )
    op.create_foreign_key(
        op.f("fk_engine_runs_instrument_id_instruments"),
        "engine_runs",
        "instruments",
        ["instrument_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("engine_runs", "contract_id")
    op.drop_column("engine_runs", "root_id")
    op.drop_column("engine_runs", "dataset_version_id")
    op.add_column(
        "manual_fib_grids", sa.Column("instrument_id", sa.BigInteger(), nullable=False)
    )
    op.drop_index(op.f("ix_manual_fib_grids_contract"), table_name="manual_fib_grids")
    op.drop_index(op.f("ix_manual_fib_grids_root"), table_name="manual_fib_grids")
    op.create_index(
        "ix_manual_fib_grids_instrument",
        "manual_fib_grids",
        ["instrument_id", "timeframe_code"],
        unique=False,
    )
    op.drop_constraint(
        op.f("fk_manual_fib_grids_root_id_roots"),
        "manual_fib_grids",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_manual_fib_grids_contract_id_contracts"),
        "manual_fib_grids",
        type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk_manual_fib_grids_instrument_id_instruments"),
        "manual_fib_grids",
        "instruments",
        ["instrument_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("manual_fib_grids", "contract_id")
    op.drop_column("manual_fib_grids", "root_id")
    # Старые таблицы удаляются разом: между ними много внешних ключей.
    op.execute(
        "DROP TABLE IF EXISTS data_import_errors, contract_step_prices, "
        "data_imports, trading_calendar_holidays, derived_builds, import_conflicts, "
        "roll_events, trading_calendars, raw_candles_1m, contract_provider_ids, "
        "roots, trading_calendar_special_days, trading_calendar_rules, "
        "import_presets, contracts, data_providers, derived_candles, "
        "dataset_versions, dataset_version_imports CASCADE"
    )
    op.execute("DROP FUNCTION IF EXISTS raw_candles_1m_guard() CASCADE")
    op.execute("DELETE FROM timeframes WHERE code = '1m'")


def downgrade() -> None:
    raise NotImplementedError(
        "Миграция 0016 необратима: контрактная модель удалена вместе с данными"
    )
