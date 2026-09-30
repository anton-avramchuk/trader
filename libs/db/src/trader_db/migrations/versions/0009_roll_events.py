"""roll events of continuous series (issue #11)

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-30 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "roll_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("root_id", sa.BigInteger(), nullable=False),
        sa.Column("from_contract_id", sa.BigInteger(), nullable=False),
        sa.Column("to_contract_id", sa.BigInteger(), nullable=False),
        sa.Column("rolled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ratio", sa.Numeric(precision=24, scale=12), nullable=False),
        sa.Column("basis_trading_day", sa.Date(), nullable=False),
        sa.Column("from_close", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("to_close", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("ratio > 0", name=op.f("ck_roll_events_ratio_positive")),
        sa.CheckConstraint(
            "from_contract_id <> to_contract_id",
            name=op.f("ck_roll_events_contracts_differ"),
        ),
        sa.CheckConstraint(
            "available_at >= rolled_at",
            name=op.f("ck_roll_events_available_after_roll"),
        ),
        sa.ForeignKeyConstraint(
            ["root_id"],
            ["roots.id"],
            name=op.f("fk_roll_events_root_id_roots"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["from_contract_id"],
            ["contracts.id"],
            name=op.f("fk_roll_events_from_contract_id_contracts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["to_contract_id"],
            ["contracts.id"],
            name=op.f("fk_roll_events_to_contract_id_contracts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roll_events")),
        sa.UniqueConstraint(
            "root_id",
            "from_contract_id",
            "to_contract_id",
            name="uq_roll_events_pair",
        ),
        sa.UniqueConstraint("root_id", "rolled_at", name="uq_roll_events_moment"),
    )


def downgrade() -> None:
    op.drop_table("roll_events")
