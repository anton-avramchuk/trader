"""base schema

Revision ID: 0001
Revises:
Create Date: 2026-09-29 18:56:04.350892
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_providers",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_providers")),
        sa.UniqueConstraint("code", name=op.f("uq_data_providers_code")),
    )
    op.create_table(
        "timeframes",
        sa.Column("code", sa.String(length=8), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "is_internal", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.CheckConstraint(
            "duration_seconds > 0", name=op.f("ck_timeframes_duration_positive")
        ),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_timeframes")),
        sa.UniqueConstraint("sort_order", name=op.f("uq_timeframes_sort_order")),
    )
    op.create_table(
        "trading_calendars",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "timezone",
            sa.String(length=64),
            server_default="Europe/Moscow",
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trading_calendars")),
        sa.UniqueConstraint("code", name=op.f("uq_trading_calendars_code")),
    )
    op.create_table(
        "roots",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=False),
        sa.Column("quote_currency", sa.String(length=3), nullable=False),
        sa.Column("tick_size", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("calendar_id", sa.BigInteger(), nullable=False),
        sa.Column("roll_trading_days", sa.Integer(), nullable=False),
        sa.Column(
            "include_weekend_sessions",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "roll_trading_days >= 0", name=op.f("ck_roots_roll_days_non_negative")
        ),
        sa.CheckConstraint("tick_size > 0", name=op.f("ck_roots_tick_size_positive")),
        sa.ForeignKeyConstraint(
            ["calendar_id"],
            ["trading_calendars.id"],
            name=op.f("fk_roots_calendar_id_trading_calendars"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roots")),
        sa.UniqueConstraint("code", name=op.f("uq_roots_code")),
    )
    op.create_table(
        "contracts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("root_id", sa.BigInteger(), nullable=False),
        sa.Column("expiration_date", sa.Date(), nullable=False),
        sa.Column("last_trade_date", sa.Date(), nullable=True),
        sa.Column("secid", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "last_trade_date IS NULL OR last_trade_date <= expiration_date",
            name=op.f("ck_contracts_last_trade_before_expiration"),
        ),
        sa.ForeignKeyConstraint(
            ["root_id"],
            ["roots.id"],
            name=op.f("fk_contracts_root_id_roots"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contracts")),
        sa.UniqueConstraint(
            "root_id", "expiration_date", name="uq_contracts_root_expiration"
        ),
    )
    op.create_table(
        "contract_provider_ids",
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("provider_id", sa.BigInteger(), nullable=False),
        sa.Column("id_type", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["contracts.id"],
            name=op.f("fk_contract_provider_ids_contract_id_contracts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["data_providers.id"],
            name=op.f("fk_contract_provider_ids_provider_id_data_providers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "contract_id",
            "provider_id",
            "id_type",
            name=op.f("pk_contract_provider_ids"),
        ),
    )
    op.create_index(
        "ix_contract_provider_ids_lookup",
        "contract_provider_ids",
        ["provider_id", "id_type", "external_id"],
        unique=False,
    )
    op.create_table(
        "contract_step_prices",
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("step_price", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.CheckConstraint(
            "step_price > 0", name=op.f("ck_contract_step_prices_step_price_positive")
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["contracts.id"],
            name=op.f("fk_contract_step_prices_contract_id_contracts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "contract_id", "date", name=op.f("pk_contract_step_prices")
        ),
    )

    _seed_reference_data()


def _seed_reference_data() -> None:
    timeframes = sa.table(
        "timeframes",
        sa.column("code", sa.String),
        sa.column("duration_seconds", sa.Integer),
        sa.column("sort_order", sa.Integer),
        sa.column("is_internal", sa.Boolean),
    )
    op.bulk_insert(
        timeframes,
        [
            {
                "code": "1m",
                "duration_seconds": 60,
                "sort_order": 1,
                "is_internal": True,
            },
            {
                "code": "15m",
                "duration_seconds": 900,
                "sort_order": 2,
                "is_internal": False,
            },
            {
                "code": "1h",
                "duration_seconds": 3600,
                "sort_order": 3,
                "is_internal": False,
            },
            {
                "code": "4h",
                "duration_seconds": 14400,
                "sort_order": 4,
                "is_internal": False,
            },
            {
                "code": "1d",
                "duration_seconds": 86400,
                "sort_order": 5,
                "is_internal": False,
            },
            {
                "code": "1w",
                "duration_seconds": 604800,
                "sort_order": 6,
                "is_internal": False,
            },
        ],
    )
    providers = sa.table(
        "data_providers", sa.column("code", sa.String), sa.column("name", sa.String)
    )
    op.bulk_insert(
        providers,
        [
            {"code": "moex_iss", "name": "MOEX ISS"},
            {"code": "tinvest", "name": "T-Invest"},
            {"code": "csv", "name": "CSV / файл"},
        ],
    )


def downgrade() -> None:
    op.drop_table("contract_step_prices")
    op.drop_index("ix_contract_provider_ids_lookup", table_name="contract_provider_ids")
    op.drop_table("contract_provider_ids")
    op.drop_table("contracts")
    op.drop_table("roots")
    op.drop_table("trading_calendars")
    op.drop_table("timeframes")
    op.drop_table("data_providers")
