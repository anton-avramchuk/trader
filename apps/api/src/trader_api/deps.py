"""Общие зависимости маршрутов: сессия БД и настройки."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncSession


class ApiSettings(BaseSettings):
    """Настройки API; переменные окружения с префиксом ``TRADER_``."""

    model_config = SettingsConfigDict(env_prefix="TRADER_", extra="ignore")

    importer_url: str = "http://127.0.0.1:8100"


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessions() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_session)]


def get_settings() -> ApiSettings:
    return ApiSettings()


Settings = Annotated[ApiSettings, Depends(get_settings)]
