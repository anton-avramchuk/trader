"""Сохраняемые пресеты сопоставления колонок файлового импорта."""

from typing import Any

from sqlalchemy import BigInteger, Boolean, Identity, String, false, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from trader_db.models.base import Base, CreatedAtMixin


class ImportPreset(CreatedAtMixin, Base):
    """Пресет — сериализованный ``trader_engine.file_import.FileMapping``.

    Встроенные (``builtin``) поставляются миграцией и не изменяются из кода.
    """

    __tablename__ = "import_presets"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str | None] = mapped_column(String(256))
    mapping: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    builtin: Mapped[bool] = mapped_column(Boolean, server_default=false())
