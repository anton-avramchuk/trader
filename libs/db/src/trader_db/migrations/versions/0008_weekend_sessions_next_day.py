"""weekend sessions belong to the next trading day (issue #10)

Сверка объёма дня в дневной истории ISS с минутными свечами показала, что
выходные сессии — часть торгового дня понедельника (расхождение 0,06%), а не
самостоятельные торговые дни. Окна выходных получают ``next_day = true``; иначе
поток торговых дней немонотонен (пн, сб, вс, пн) и агрегация дублирует бары.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-30 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _set_next_day(value: bool) -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE trading_calendar_rules r "
            "SET weekend_windows = ("
            "  SELECT COALESCE(jsonb_agg(w || CAST(:flag AS jsonb)), '[]'::jsonb) "
            "  FROM jsonb_array_elements(r.weekend_windows) AS w"
            ") WHERE jsonb_array_length(r.weekend_windows) > 0"
        ),
        {"flag": '{"next_day": true}' if value else '{"next_day": false}'},
    )


def upgrade() -> None:
    _set_next_day(True)


def downgrade() -> None:
    _set_next_day(False)
