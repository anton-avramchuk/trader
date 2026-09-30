"""manual fibonacci grids (issue #37)

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-30 17:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "manual_fib_grids",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=True),
        sa.Column("root_id", sa.BigInteger(), nullable=True),
        sa.Column("timeframe_code", sa.String(length=8), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_price", sa.Numeric(24, 8), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_price", sa.Numeric(24, 8), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(contract_id IS NULL) <> (root_id IS NULL)",
            name=op.f("ck_manual_fib_grids_one_subject"),
        ),
        sa.CheckConstraint(
            "start_time <> end_time", name=op.f("ck_manual_fib_grids_distinct_times")
        ),
        sa.CheckConstraint(
            "start_price <> end_price",
            name=op.f("ck_manual_fib_grids_distinct_prices"),
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["contracts.id"],
            name=op.f("fk_manual_fib_grids_contract_id_contracts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["root_id"],
            ["roots.id"],
            name=op.f("fk_manual_fib_grids_root_id_roots"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["timeframe_code"],
            ["timeframes.code"],
            name=op.f("fk_manual_fib_grids_timeframe_code_timeframes"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_manual_fib_grids")),
    )
    op.create_index(
        "ix_manual_fib_grids_contract",
        "manual_fib_grids",
        ["contract_id", "timeframe_code"],
    )
    op.create_index(
        "ix_manual_fib_grids_root", "manual_fib_grids", ["root_id", "timeframe_code"]
    )


def downgrade() -> None:
    op.drop_index("ix_manual_fib_grids_root", table_name="manual_fib_grids")
    op.drop_index("ix_manual_fib_grids_contract", table_name="manual_fib_grids")
    op.drop_table("manual_fib_grids")
