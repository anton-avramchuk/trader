"""chart profiles (issue #31)

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-30 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chart_profiles",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("root_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["root_id"],
            ["roots.id"],
            name=op.f("fk_chart_profiles_root_id_roots"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chart_profiles")),
    )
    op.create_index(
        "uq_chart_profiles_global_name",
        "chart_profiles",
        ["name"],
        unique=True,
        postgresql_where=sa.text("root_id IS NULL"),
    )
    op.create_index(
        "uq_chart_profiles_root_name",
        "chart_profiles",
        ["root_id", "name"],
        unique=True,
        postgresql_where=sa.text("root_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_chart_profiles_root_name", table_name="chart_profiles")
    op.drop_index("uq_chart_profiles_global_name", table_name="chart_profiles")
    op.drop_table("chart_profiles")
