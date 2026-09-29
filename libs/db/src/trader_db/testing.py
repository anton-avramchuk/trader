"""Помощники для интеграционных тестов (используются тестами db, worker и api)."""

from collections.abc import Generator
from contextlib import contextmanager
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


@contextmanager
def temporary_database(base_url: str) -> Generator[str]:
    """Создать пустую БД ``trader_test_<uuid>`` на сервере ``base_url``.

    Возвращает URL новой БД; по выходу БД удаляется. Тесты, меняющие схему или
    данные, никогда не должны работать с рабочей БД.
    """
    url = make_url(base_url)
    name = f"trader_test_{uuid4().hex[:12]}"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield url.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
