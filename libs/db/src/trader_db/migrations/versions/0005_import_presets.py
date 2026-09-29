"""import presets

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-29 20:15:03.294437
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_presets",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=256), nullable=True),
        sa.Column(
            "mapping",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "builtin", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_presets")),
        sa.UniqueConstraint("name", name=op.f("uq_import_presets_name")),
    )

    _seed_builtin_presets()


def _seed_builtin_presets() -> None:
    """Встроенные пресеты (формат Finam — по стандартной выгрузке, не сверен с файлом)."""
    common = {
        "encoding": "utf-8-sig",
        "skip_rows": 0,
        "decimal_separator": ".",
        "timezone": "Europe/Moscow",
        "trade_count": None,
        "quote_volume": None,
        "timestamp_is_close": False,
    }
    presets = sa.table(
        "import_presets",
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("mapping", postgresql.JSONB),
        sa.column("builtin", sa.Boolean),
    )
    op.bulk_insert(
        presets,
        [
            {
                "name": "finam",
                "description": "Finam, выгрузка с заголовком: <DATE>,<TIME>,<OPEN>… "
                "время московское, начало свечи",
                "builtin": True,
                "mapping": {
                    **common,
                    "delimiter": ",",
                    "has_header": True,
                    "datetime": {
                        "columns": ["<DATE>", "<TIME>"],
                        "format": "%Y%m%d %H%M%S",
                    },
                    "open": "<OPEN>",
                    "high": "<HIGH>",
                    "low": "<LOW>",
                    "close": "<CLOSE>",
                    "volume": "<VOL>",
                },
            },
            {
                "name": "finam_no_header",
                "description": "Finam, выгрузка без заголовка: "
                "TICKER,PER,DATE,TIME,OPEN,HIGH,LOW,CLOSE,VOL",
                "builtin": True,
                "mapping": {
                    **common,
                    "delimiter": ",",
                    "has_header": False,
                    "datetime": {"columns": [2, 3], "format": "%Y%m%d %H%M%S"},
                    "open": 4,
                    "high": 5,
                    "low": 6,
                    "close": 7,
                    "volume": 8,
                },
            },
            {
                "name": "iso_utc",
                "description": "Простой CSV: timestamp (ISO 8601, UTC), open, high, "
                "low, close, volume",
                "builtin": True,
                "mapping": {
                    **common,
                    "timezone": "UTC",
                    "delimiter": ",",
                    "has_header": True,
                    "datetime": {"columns": ["timestamp"], "format": "iso"},
                    "open": "open",
                    "high": "high",
                    "low": "low",
                    "close": "close",
                    "volume": "volume",
                },
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("import_presets")
