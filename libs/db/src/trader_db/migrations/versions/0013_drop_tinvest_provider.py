"""remove T-Invest from data providers (ADR-0024)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-01 09:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM data_providers WHERE code = 'tinvest'")


def downgrade() -> None:
    op.execute("INSERT INTO data_providers (code, name) VALUES ('tinvest', 'T-Invest')")
