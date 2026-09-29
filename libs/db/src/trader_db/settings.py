from pydantic_settings import BaseSettings, SettingsConfigDict


class DbSettings(BaseSettings):
    """Настройки подключения; переменные окружения с префиксом ``TRADER_``."""

    model_config = SettingsConfigDict(env_prefix="TRADER_", extra="ignore")

    database_url: str = "postgresql+psycopg://trader:trader@127.0.0.1:5432/trader"
