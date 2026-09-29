from alembic import context
from sqlalchemy import create_engine

from trader_db.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url")
    assert url is not None, "sqlalchemy.url не задан"
    connectable = create_engine(url)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Офлайн-режим миграций не поддерживается")

run_migrations_online()
