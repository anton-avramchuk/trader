"""derived candles and builds

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-29 21:44:46.136383
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "derived_builds",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("timeframe_code", sa.String(length=8), nullable=False),
        sa.Column("source_dataset_version_id", sa.BigInteger(), nullable=False),
        sa.Column("calendar_hash", sa.String(length=64), nullable=False),
        sa.Column("include_weekend_sessions", sa.Boolean(), nullable=False),
        sa.Column("algorithm_version", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("rebuilt_from_day", sa.Date(), nullable=True),
        sa.Column("complete_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rows_deleted", sa.BigInteger(), nullable=False),
        sa.Column("rows_written", sa.BigInteger(), nullable=False),
        sa.Column(
            "report",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "mode IN ('full', 'incremental')", name=op.f("ck_derived_builds_mode_valid")
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["contracts.id"],
            name=op.f("fk_derived_builds_contract_id_contracts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_dataset_version_id"],
            ["dataset_versions.id"],
            name=op.f("fk_derived_builds_source_dataset_version_id_dataset_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["timeframe_code"],
            ["timeframes.code"],
            name=op.f("fk_derived_builds_timeframe_code_timeframes"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_derived_builds")),
    )
    op.create_index(
        "ix_derived_builds_contract_timeframe",
        "derived_builds",
        ["contract_id", "timeframe_code", "id"],
        unique=False,
    )
    op.create_table(
        "derived_candles",
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("timeframe_code", sa.String(length=8), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("close_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("high", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("low", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("close", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("volume", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("trade_count", sa.BigInteger(), nullable=True),
        sa.Column("candles", sa.Integer(), nullable=False),
        sa.Column("is_partial", sa.Boolean(), nullable=False),
        sa.Column("trading_day", sa.Date(), nullable=False),
        sa.Column("built_from_version_id", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "candles >= 1", name=op.f("ck_derived_candles_candles_positive")
        ),
        sa.CheckConstraint(
            "close_time > timestamp", name=op.f("ck_derived_candles_close_after_open")
        ),
        sa.CheckConstraint("high >= low", name=op.f("ck_derived_candles_high_ge_low")),
        sa.CheckConstraint(
            "high >= open AND high >= close",
            name=op.f("ck_derived_candles_high_ge_open_close"),
        ),
        sa.CheckConstraint(
            "low <= open AND low <= close",
            name=op.f("ck_derived_candles_low_le_open_close"),
        ),
        sa.CheckConstraint(
            "volume >= 0", name=op.f("ck_derived_candles_volume_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["built_from_version_id"],
            ["dataset_versions.id"],
            name=op.f("fk_derived_candles_built_from_version_id_dataset_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["contracts.id"],
            name=op.f("fk_derived_candles_contract_id_contracts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["timeframe_code"],
            ["timeframes.code"],
            name=op.f("fk_derived_candles_timeframe_code_timeframes"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "contract_id", "timeframe_code", "timestamp", name="pk_derived_candles"
        ),
    )
    op.create_index(
        "ix_derived_candles_trading_day",
        "derived_candles",
        ["contract_id", "timeframe_code", "trading_day"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_derived_candles_trading_day", table_name="derived_candles")
    op.drop_table("derived_candles")
    op.drop_index("ix_derived_builds_contract_timeframe", table_name="derived_builds")
    op.drop_table("derived_builds")
