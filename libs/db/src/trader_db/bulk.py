"""Массовая вставка через ``COPY`` (примерно в 5 раз быстрее executemany)."""

from collections.abc import Iterable, Sequence
from typing import Any, cast

import psycopg
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session


def copy_rows(
    session: Session,
    table: str,
    columns: Sequence[str],
    rows: Iterable[Sequence[Any]],
) -> None:
    """Вставить строки в ``table`` в транзакции сессии.

    Исключения ``psycopg`` переводятся в ``DBAPIError`` SQLAlchemy — как при
    обычной вставке, вызывающий ловит один и тот же тип.
    """
    session.flush()
    driver = cast(Any, session.connection().connection.driver_connection)
    statement = f"COPY {table} ({', '.join(columns)}) FROM STDIN"
    try:
        with driver.cursor() as cursor, cursor.copy(statement) as copy:
            for row in rows:
                copy.write_row(tuple(row))
    except psycopg.Error as error:
        raise DBAPIError.instance(statement, None, error, psycopg.Error) from error
