"""raw candles imports and dataset versions

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-29 19:11:54.298415
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_imports",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("provider_id", sa.BigInteger(), nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "timeframe_code", sa.String(length=8), server_default="1m", nullable=False
        ),
        sa.Column(
            "kind", sa.String(length=32), server_default="import", nullable=False
        ),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_name", sa.String(length=512), nullable=True),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(length=16), server_default="running", nullable=False
        ),
        sa.Column(
            "report",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("resolves_import_id", sa.BigInteger(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('import', 'conflict_resolution')",
            name=op.f("ck_data_imports_kind_valid"),
        ),
        sa.CheckConstraint(
            "source_type IN ('file', 'api', 'manual')",
            name=op.f("ck_data_imports_source_type_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name=op.f("ck_data_imports_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["contracts.id"],
            name=op.f("fk_data_imports_contract_id_contracts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["data_providers.id"],
            name=op.f("fk_data_imports_provider_id_data_providers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolves_import_id"],
            ["data_imports.id"],
            name=op.f("fk_data_imports_resolves_import_id_data_imports"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["timeframe_code"],
            ["timeframes.code"],
            name=op.f("fk_data_imports_timeframe_code_timeframes"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_imports")),
    )
    op.create_index(
        op.f("ix_data_imports_contract_id"),
        "data_imports",
        ["contract_id"],
        unique=False,
    )
    op.create_table(
        "data_import_errors",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("data_import_id", sa.BigInteger(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=True),
        sa.Column("raw_row", sa.Text(), nullable=True),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["data_import_id"],
            ["data_imports.id"],
            name=op.f("fk_data_import_errors_data_import_id_data_imports"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_import_errors")),
    )
    op.create_index(
        op.f("ix_data_import_errors_data_import_id"),
        "data_import_errors",
        ["data_import_id"],
        unique=False,
    )
    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("timeframe_code", sa.String(length=8), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("range_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("range_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        sa.Column("created_by_import_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["contracts.id"],
            name=op.f("fk_dataset_versions_contract_id_contracts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_import_id"],
            ["data_imports.id"],
            name=op.f("fk_dataset_versions_created_by_import_id_data_imports"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["timeframe_code"],
            ["timeframes.code"],
            name=op.f("fk_dataset_versions_timeframe_code_timeframes"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_versions")),
        sa.UniqueConstraint(
            "contract_id",
            "timeframe_code",
            "version_number",
            name="uq_dataset_versions_number",
        ),
    )
    op.create_table(
        "raw_candles_1m",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("high", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("low", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("close", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("volume", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("trade_count", sa.Integer(), nullable=True),
        sa.Column("quote_volume", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("data_import_id", sa.BigInteger(), nullable=False),
        sa.Column("superseded_by_import_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("high >= low", name=op.f("ck_raw_candles_1m_high_ge_low")),
        sa.CheckConstraint(
            "high >= open AND high >= close",
            name=op.f("ck_raw_candles_1m_high_ge_open_close"),
        ),
        sa.CheckConstraint(
            "low <= open AND low <= close",
            name=op.f("ck_raw_candles_1m_low_le_open_close"),
        ),
        sa.CheckConstraint(
            "volume >= 0", name=op.f("ck_raw_candles_1m_volume_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["contracts.id"],
            name=op.f("fk_raw_candles_1m_contract_id_contracts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["data_import_id"],
            ["data_imports.id"],
            name=op.f("fk_raw_candles_1m_data_import_id_data_imports"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_import_id"],
            ["data_imports.id"],
            name=op.f("fk_raw_candles_1m_superseded_by_import_id_data_imports"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_raw_candles_1m")),
    )
    op.create_index(
        "ix_raw_candles_1m_contract_timestamp",
        "raw_candles_1m",
        ["contract_id", "timestamp"],
        unique=False,
    )
    op.create_index(
        "ix_raw_candles_1m_data_import_id",
        "raw_candles_1m",
        ["data_import_id"],
        unique=False,
    )
    op.create_index(
        "uq_raw_candles_1m_active",
        "raw_candles_1m",
        ["contract_id", "timestamp"],
        unique=True,
        postgresql_where=sa.text("superseded_by_import_id IS NULL"),
    )
    op.create_table(
        "dataset_version_imports",
        sa.Column("dataset_version_id", sa.BigInteger(), nullable=False),
        sa.Column("data_import_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["data_import_id"],
            ["data_imports.id"],
            name=op.f("fk_dataset_version_imports_data_import_id_data_imports"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_versions.id"],
            name=op.f("fk_dataset_version_imports_dataset_version_id_dataset_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "dataset_version_id",
            "data_import_id",
            name=op.f("pk_dataset_version_imports"),
        ),
    )
    op.create_table(
        "import_conflicts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("data_import_id", sa.BigInteger(), nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("existing_candle_id", sa.BigInteger(), nullable=False),
        sa.Column("new_open", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("new_high", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("new_low", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("new_close", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("new_volume", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("new_trade_count", sa.Integer(), nullable=True),
        sa.Column("new_quote_volume", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default="pending", nullable=False
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_import_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')",
            name=op.f("ck_import_conflicts_status_valid"),
        ),
        sa.CheckConstraint(
            "new_high >= new_low", name=op.f("ck_import_conflicts_new_high_ge_low")
        ),
        sa.CheckConstraint(
            "new_high >= new_open AND new_high >= new_close",
            name=op.f("ck_import_conflicts_new_high_ge_open_close"),
        ),
        sa.CheckConstraint(
            "new_low <= new_open AND new_low <= new_close",
            name=op.f("ck_import_conflicts_new_low_le_open_close"),
        ),
        sa.CheckConstraint(
            "new_volume >= 0", name=op.f("ck_import_conflicts_new_volume_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["contracts.id"],
            name=op.f("fk_import_conflicts_contract_id_contracts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["data_import_id"],
            ["data_imports.id"],
            name=op.f("fk_import_conflicts_data_import_id_data_imports"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["existing_candle_id"],
            ["raw_candles_1m.id"],
            name=op.f("fk_import_conflicts_existing_candle_id_raw_candles_1m"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolution_import_id"],
            ["data_imports.id"],
            name=op.f("fk_import_conflicts_resolution_import_id_data_imports"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_conflicts")),
        sa.UniqueConstraint(
            "data_import_id", "contract_id", "timestamp", name="uq_import_conflicts_key"
        ),
    )
    op.create_index(
        "ix_import_conflicts_pending",
        "import_conflicts",
        ["contract_id", "timestamp"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )

    _create_immutability_triggers()


def _create_immutability_triggers() -> None:
    """Инварианты ADR-0005 на уровне БД, а не только кода приложения."""
    op.execute(
        """
        CREATE FUNCTION raw_candles_1m_guard() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'raw_candles_1m: удаление запрещено (ADR-0005)';
            END IF;
            IF ROW(NEW.id, NEW.contract_id, NEW.timestamp, NEW.open, NEW.high, NEW.low,
                   NEW.close, NEW.volume, NEW.trade_count, NEW.quote_volume,
                   NEW.data_import_id, NEW.created_at)
               IS DISTINCT FROM
               ROW(OLD.id, OLD.contract_id, OLD.timestamp, OLD.open, OLD.high, OLD.low,
                   OLD.close, OLD.volume, OLD.trade_count, OLD.quote_volume,
                   OLD.data_import_id, OLD.created_at) THEN
                RAISE EXCEPTION
                    'raw_candles_1m: значения неизменяемы, допустимо только superseded_by_import_id';
            END IF;
            IF OLD.superseded_by_import_id IS NOT NULL THEN
                RAISE EXCEPTION 'raw_candles_1m: свеча уже замещена';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER raw_candles_1m_guard BEFORE UPDATE OR DELETE ON raw_candles_1m
        FOR EACH ROW EXECUTE FUNCTION raw_candles_1m_guard()
        """
    )
    op.execute(
        """
        CREATE FUNCTION forbid_modification() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '%: изменение и удаление запрещены (ADR-0005)', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in ("dataset_versions", "dataset_version_imports"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION forbid_modification()"
        )


def downgrade() -> None:
    op.drop_index(
        "ix_import_conflicts_pending",
        table_name="import_conflicts",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_table("import_conflicts")
    op.drop_table("dataset_version_imports")
    op.drop_index(
        "uq_raw_candles_1m_active",
        table_name="raw_candles_1m",
        postgresql_where=sa.text("superseded_by_import_id IS NULL"),
    )
    op.drop_index("ix_raw_candles_1m_data_import_id", table_name="raw_candles_1m")
    op.drop_index("ix_raw_candles_1m_contract_timestamp", table_name="raw_candles_1m")
    op.drop_table("raw_candles_1m")
    op.drop_table("dataset_versions")
    op.drop_index(
        op.f("ix_data_import_errors_data_import_id"), table_name="data_import_errors"
    )
    op.drop_table("data_import_errors")
    op.drop_index(op.f("ix_data_imports_contract_id"), table_name="data_imports")
    op.drop_table("data_imports")
    op.execute("DROP FUNCTION raw_candles_1m_guard()")
    op.execute("DROP FUNCTION forbid_modification()")
