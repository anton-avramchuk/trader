"""job queue and schedules

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-29 19:21:44.087023
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_schedules",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "interval_seconds > 0", name=op.f("ck_job_schedules_interval_positive")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_schedules")),
        sa.UniqueConstraint("name", name=op.f("uq_job_schedules_name")),
    )
    op.create_index(
        "ix_job_schedules_due",
        "job_schedules",
        ["next_run_at"],
        unique=False,
        postgresql_where=sa.text("enabled"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(length=16), server_default="queued", nullable=False
        ),
        sa.Column("progress", sa.Float(), server_default="0", nullable=False),
        sa.Column("progress_message", sa.String(length=256), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("locked_by", sa.String(length=128), nullable=True),
        sa.Column(
            "run_after",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("schedule_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_jobs_status_valid"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_jobs_attempts_non_negative")),
        sa.CheckConstraint(
            "max_attempts >= 1", name=op.f("ck_jobs_max_attempts_positive")
        ),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 1", name=op.f("ck_jobs_progress_range")
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["job_schedules.id"],
            name=op.f("fk_jobs_schedule_id_job_schedules"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_index(
        "ix_jobs_queued",
        "jobs",
        ["run_after", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_jobs_running_heartbeat",
        "jobs",
        ["heartbeat_at"],
        unique=False,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index("ix_jobs_schedule_id", "jobs", ["schedule_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_jobs_schedule_id", table_name="jobs")
    op.drop_index(
        "ix_jobs_running_heartbeat",
        table_name="jobs",
        postgresql_where=sa.text("status = 'running'"),
    )
    op.drop_index(
        "ix_jobs_queued",
        table_name="jobs",
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.drop_table("jobs")
    op.drop_index(
        "ix_job_schedules_due",
        table_name="job_schedules",
        postgresql_where=sa.text("enabled"),
    )
    op.drop_table("job_schedules")
