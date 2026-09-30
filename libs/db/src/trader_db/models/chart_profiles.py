"""Профили графика: набор индикаторов с source TF, слои и стили (ADR-0010)."""

import datetime as dt
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from trader_db.models.base import Base, CreatedAtMixin


class ChartProfile(CreatedAtMixin, Base):
    """Именованный профиль графика: глобальный (``root_id`` пуст) или для root.

    Имена уникальны в своей области: среди глобальных и среди профилей одного root.
    ``last_used_at`` запоминает последний применённый профиль.
    """

    __tablename__ = "chart_profiles"
    __table_args__ = (
        Index(
            "uq_chart_profiles_global_name",
            "name",
            unique=True,
            postgresql_where=text("root_id IS NULL"),
        ),
        Index(
            "uq_chart_profiles_root_name",
            "root_id",
            "name",
            unique=True,
            postgresql_where=text("root_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    root_id: Mapped[int | None] = mapped_column(
        ForeignKey("roots.id", ondelete="CASCADE")
    )
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
