"""Общие зависимости маршрутов: сессия БД и каталог загруженных файлов."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncSession


class ApiSettings(BaseSettings):
    """Настройки API; каталог файлов общий с worker (``TRADER_IMPORT_DIR``)."""

    model_config = SettingsConfigDict(env_prefix="TRADER_", extra="ignore")

    import_dir: Path = Path("data/imports")
    max_upload_bytes: int = 512 * 1024 * 1024


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessions() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_session)]


def get_settings() -> ApiSettings:
    return ApiSettings()


Settings = Annotated[ApiSettings, Depends(get_settings)]
