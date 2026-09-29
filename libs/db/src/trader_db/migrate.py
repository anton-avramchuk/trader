from pathlib import Path

from alembic import command
from alembic.config import Config

from trader_db.settings import DbSettings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(url: str | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "sqlalchemy.url",
        (url if url is not None else DbSettings().database_url).replace("%", "%%"),
    )
    return config


def upgrade_head(url: str | None = None) -> None:
    """Применить все миграции."""
    command.upgrade(alembic_config(url), "head")
