"""Bitemporal raw-данные 1m, импорты, конфликты и dataset versions (ADR-0005)."""

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from trader_db.models.base import PRICE, Base, CreatedAtMixin

VOLUME = Numeric(24, 8)

IMPORT_KINDS = ("import", "conflict_resolution")
IMPORT_STATUSES = ("running", "completed", "failed")
CONFLICT_STATUSES = ("pending", "accepted", "rejected")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class DataImport(CreatedAtMixin, Base):
    """Одна операция импорта. Разрешение конфликтов — тоже импорт (свой id)."""

    __tablename__ = "data_imports"
    __table_args__ = (
        CheckConstraint(_in_list("kind", IMPORT_KINDS), name="kind_valid"),
        CheckConstraint(_in_list("status", IMPORT_STATUSES), name="status_valid"),
        CheckConstraint(
            "source_type IN ('file', 'api', 'manual')", name="source_type_valid"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("data_providers.id", ondelete="RESTRICT")
    )
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("contracts.id", ondelete="RESTRICT"), index=True
    )
    timeframe_code: Mapped[str] = mapped_column(
        ForeignKey("timeframes.code", ondelete="RESTRICT"), server_default="1m"
    )
    kind: Mapped[str] = mapped_column(String(32), server_default="import")
    source_type: Mapped[str] = mapped_column(String(16))
    source_name: Mapped[str | None] = mapped_column(String(512))
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(16), server_default="running")
    report: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(Text)
    resolves_import_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_imports.id", ondelete="RESTRICT")
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class DataImportError(CreatedAtMixin, Base):
    """Отклонённая строка импорта: ошибочные строки не отбрасываются молча."""

    __tablename__ = "data_import_errors"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    data_import_id: Mapped[int] = mapped_column(
        ForeignKey("data_imports.id", ondelete="CASCADE"), index=True
    )
    row_number: Mapped[int | None] = mapped_column(Integer)
    raw_row: Mapped[str | None] = mapped_column(Text)
    reason_code: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)


class RawCandle1m(CreatedAtMixin, Base):
    """Минутная raw-свеча. Значения неизменяемы; замещение — через supersede.

    Триггер БД разрешает единственное изменение: один раз проставить
    ``superseded_by_import_id``. Удалять строки нельзя.
    """

    __tablename__ = "raw_candles_1m"
    __table_args__ = (
        CheckConstraint("high >= low", name="high_ge_low"),
        CheckConstraint("high >= open AND high >= close", name="high_ge_open_close"),
        CheckConstraint("low <= open AND low <= close", name="low_le_open_close"),
        CheckConstraint("volume >= 0", name="volume_non_negative"),
        # Для каждой свечи ровно одна активная (не замещённая) строка.
        Index(
            "uq_raw_candles_1m_active",
            "contract_id",
            "timestamp",
            unique=True,
            postgresql_where=text("superseded_by_import_id IS NULL"),
        ),
        Index("ix_raw_candles_1m_contract_timestamp", "contract_id", "timestamp"),
        Index("ix_raw_candles_1m_data_import_id", "data_import_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("contracts.id", ondelete="RESTRICT")
    )
    # Время открытия свечи, UTC (ADR-0004).
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    open: Mapped[Decimal] = mapped_column(PRICE)
    high: Mapped[Decimal] = mapped_column(PRICE)
    low: Mapped[Decimal] = mapped_column(PRICE)
    close: Mapped[Decimal] = mapped_column(PRICE)
    volume: Mapped[Decimal] = mapped_column(VOLUME)
    trade_count: Mapped[int | None] = mapped_column(Integer)
    quote_volume: Mapped[Decimal | None] = mapped_column(VOLUME)
    data_import_id: Mapped[int] = mapped_column(
        ForeignKey("data_imports.id", ondelete="RESTRICT")
    )
    superseded_by_import_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_imports.id", ondelete="RESTRICT")
    )


class ImportConflict(Base):
    """Свеча из импорта, отличающаяся от активной. Не применяется без решения."""

    __tablename__ = "import_conflicts"
    __table_args__ = (
        CheckConstraint(_in_list("status", CONFLICT_STATUSES), name="status_valid"),
        # Те же инварианты, что у raw-свечи: невалидное значение не должно ждать
        # решения пользователя.
        CheckConstraint("new_high >= new_low", name="new_high_ge_low"),
        CheckConstraint(
            "new_high >= new_open AND new_high >= new_close",
            name="new_high_ge_open_close",
        ),
        CheckConstraint(
            "new_low <= new_open AND new_low <= new_close",
            name="new_low_le_open_close",
        ),
        CheckConstraint("new_volume >= 0", name="new_volume_non_negative"),
        UniqueConstraint(
            "data_import_id", "contract_id", "timestamp", name="uq_import_conflicts_key"
        ),
        Index(
            "ix_import_conflicts_pending",
            "contract_id",
            "timestamp",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    data_import_id: Mapped[int] = mapped_column(
        ForeignKey("data_imports.id", ondelete="CASCADE")
    )
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("contracts.id", ondelete="RESTRICT")
    )
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    # Активная строка на момент обнаружения конфликта (справочно).
    existing_candle_id: Mapped[int] = mapped_column(
        ForeignKey("raw_candles_1m.id", ondelete="RESTRICT")
    )
    new_open: Mapped[Decimal] = mapped_column(PRICE)
    new_high: Mapped[Decimal] = mapped_column(PRICE)
    new_low: Mapped[Decimal] = mapped_column(PRICE)
    new_close: Mapped[Decimal] = mapped_column(PRICE)
    new_volume: Mapped[Decimal] = mapped_column(VOLUME)
    new_trade_count: Mapped[int | None] = mapped_column(Integer)
    new_quote_volume: Mapped[Decimal | None] = mapped_column(VOLUME)
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_import_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_imports.id", ondelete="RESTRICT")
    )


class DatasetVersion(CreatedAtMixin, Base):
    """Неизменяемый манифест: контракт + TF + набор импортов (+ диапазон и число строк).

    Содержимое версии однозначно восстанавливается из манифеста; копий данных нет.
    Триггер БД запрещает изменять и удалять версии и их манифесты.
    """

    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint(
            "contract_id",
            "timeframe_code",
            "version_number",
            name="uq_dataset_versions_number",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("contracts.id", ondelete="RESTRICT")
    )
    timeframe_code: Mapped[str] = mapped_column(
        ForeignKey("timeframes.code", ondelete="RESTRICT")
    )
    version_number: Mapped[int] = mapped_column(Integer)
    range_start: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    range_end: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    row_count: Mapped[int] = mapped_column(BigInteger)
    created_by_import_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_imports.id", ondelete="RESTRICT")
    )


class DatasetVersionImport(Base):
    """Импорт, входящий в манифест версии."""

    __tablename__ = "dataset_version_imports"

    dataset_version_id: Mapped[int] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), primary_key=True
    )
    data_import_id: Mapped[int] = mapped_column(
        ForeignKey("data_imports.id", ondelete="RESTRICT"), primary_key=True
    )
