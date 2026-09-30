"""engine runs and immutable event log (issue #33)

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-30 16:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "engine_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("engine", sa.String(length=64), nullable=False),
        sa.Column("algorithm_version", sa.Integer(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("params_hash", sa.String(length=64), nullable=False),
        sa.Column("contract_id", sa.BigInteger(), nullable=True),
        sa.Column("root_id", sa.BigInteger(), nullable=True),
        sa.Column("timeframe_code", sa.String(length=8), nullable=False),
        sa.Column("dataset_version_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "bars_processed",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_close_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "input_fingerprint", sa.Text(), server_default=sa.text("''"), nullable=False
        ),
        sa.Column(
            "state",
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
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(contract_id IS NULL) <> (root_id IS NULL)",
            name=op.f("ck_engine_runs_one_subject"),
        ),
        sa.CheckConstraint(
            "bars_processed >= 0", name=op.f("ck_engine_runs_bars_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["contracts.id"],
            name=op.f("fk_engine_runs_contract_id_contracts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["root_id"],
            ["roots.id"],
            name=op.f("fk_engine_runs_root_id_roots"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["timeframe_code"],
            ["timeframes.code"],
            name=op.f("fk_engine_runs_timeframe_code_timeframes"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_versions.id"],
            name=op.f("fk_engine_runs_dataset_version_id_dataset_versions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_engine_runs")),
    )
    op.create_index(
        "ix_engine_runs_key",
        "engine_runs",
        ["engine", "params_hash", "algorithm_version", "timeframe_code", "id"],
    )

    op.create_table(
        "engine_events",
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revises_seq", sa.Integer(), nullable=True),
        sa.Column("dataset_version_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "status IN ('detected', 'confirmed', 'revised', 'invalidated')",
            name=op.f("ck_engine_events_status_valid"),
        ),
        sa.CheckConstraint(
            "(status IN ('revised', 'invalidated')) = (revises_seq IS NOT NULL)"
            " OR status = 'confirmed'",
            name=op.f("ck_engine_events_revises_matches_status"),
        ),
        sa.CheckConstraint(
            "revises_seq < seq", name=op.f("ck_engine_events_revises_earlier")
        ),
        sa.CheckConstraint(
            "detected_at <= available_at",
            name=op.f("ck_engine_events_detected_before_available"),
        ),
        sa.CheckConstraint(
            "confirmed_at IS NULL OR "
            "(confirmed_at >= detected_at AND confirmed_at <= available_at)",
            name=op.f("ck_engine_events_confirmed_in_range"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["engine_runs.id"],
            name=op.f("fk_engine_events_run_id_engine_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "revises_seq"],
            ["engine_events.run_id", "engine_events.seq"],
            name=op.f("fk_engine_events_run_id_engine_events"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_versions.id"],
            name=op.f("fk_engine_events_dataset_version_id_dataset_versions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("run_id", "seq", name="pk_engine_events"),
    )
    op.create_index(
        "ix_engine_events_available", "engine_events", ["run_id", "available_at"]
    )
    # Событие не правится и не удаляется (ADR-0003): пересмотр — новое событие.
    op.execute(
        "CREATE TRIGGER engine_events_immutable BEFORE UPDATE OR DELETE "
        "ON engine_events FOR EACH ROW EXECUTE FUNCTION forbid_modification()"
    )


def downgrade() -> None:
    op.drop_table("engine_events")
    op.drop_index("ix_engine_runs_key", table_name="engine_runs")
    op.drop_table("engine_runs")
