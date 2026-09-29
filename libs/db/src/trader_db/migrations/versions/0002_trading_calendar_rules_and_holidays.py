"""trading calendar rules and holidays

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-29 19:05:20.788571
"""

from collections.abc import Sequence
from datetime import date, time

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Нужно для exclusion-constraint по (calendar_id =, daterange &&).
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table(
        "trading_calendar_holidays",
        sa.Column("calendar_id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(
            ["calendar_id"],
            ["trading_calendars.id"],
            name=op.f("fk_trading_calendar_holidays_calendar_id_trading_calendars"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "calendar_id", "date", name=op.f("pk_trading_calendar_holidays")
        ),
    )
    op.create_table(
        "trading_calendar_rules",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("calendar_id", sa.BigInteger(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("bar_anchor", sa.Time(), nullable=False),
        sa.Column(
            "weekday_windows", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "weekend_windows",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        postgresql.ExcludeConstraint(
            (sa.column("calendar_id"), "="),
            (sa.text("daterange(effective_from, effective_to, '[]')"), "&&"),
            using="gist",
            name="ex_trading_calendar_rules_no_overlap",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name=op.f("ck_trading_calendar_rules_period_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["calendar_id"],
            ["trading_calendars.id"],
            name=op.f("fk_trading_calendar_rules_calendar_id_trading_calendars"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trading_calendar_rules")),
    )

    _seed_moex_forts()


def _window(name: str, start: str, end: str, *, next_day: bool = False) -> dict:
    return {"name": name, "start": start, "end": end, "next_day": next_day}


def _seed_moex_forts() -> None:
    """Начальное расписание FORTS; режим до 23.03.2026 не проверен (см. ADR-0014)."""
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "INSERT INTO trading_calendars (code, name, timezone) "
            "VALUES ('moex_forts', 'MOEX FORTS', 'Europe/Moscow') "
            "ON CONFLICT (code) DO NOTHING"
        )
    )
    calendar_id = connection.execute(
        sa.text("SELECT id FROM trading_calendars WHERE code = 'moex_forts'")
    ).scalar_one()
    rules = sa.table(
        "trading_calendar_rules",
        sa.column("calendar_id", sa.BigInteger),
        sa.column("effective_from", sa.Date),
        sa.column("effective_to", sa.Date),
        sa.column("bar_anchor", sa.Time),
        sa.column("weekday_windows", postgresql.JSONB),
        sa.column("weekend_windows", postgresql.JSONB),
    )
    op.bulk_insert(
        rules,
        [
            {
                "calendar_id": calendar_id,
                "effective_from": date(2020, 1, 1),
                "effective_to": date(2026, 3, 22),
                "bar_anchor": time(7, 0),
                "weekday_windows": [
                    _window("morning", "07:00", "09:50"),
                    _window("main", "10:00", "14:00"),
                    _window("main_2", "14:05", "18:45"),
                    _window("evening", "19:05", "23:50", next_day=True),
                ],
                "weekend_windows": [],
            },
            {
                "calendar_id": calendar_id,
                "effective_from": date(2026, 3, 23),
                "effective_to": None,
                "bar_anchor": time(7, 0),
                "weekday_windows": [
                    _window("morning", "07:00", "09:00"),
                    _window("main", "09:00", "19:00"),
                    _window("evening", "19:00", "23:50"),
                ],
                "weekend_windows": [_window("weekend", "10:00", "19:00")],
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("trading_calendar_rules")
    op.drop_table("trading_calendar_holidays")
