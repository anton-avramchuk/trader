"""Запись raw-свечей 1m: импорты, дубликаты, конфликты и их разрешение (ADR-0005).

Правила:
- в raw-таблицу свеча попадает, только если для ``(contract, timestamp)`` нет
  активной строки;
- идентичная активной свеча — дубликат, ничего не пишется;
- отличающаяся — конфликт (``import_conflicts``), данные не меняются, пока
  пользователь не примет решение;
- принятие конфликтов оформляется **отдельным импортом-разрешением**: новые
  строки получают его id, а старые замещаются им. Поэтому ранее созданные
  dataset version продолжают возвращать прежние данные.
"""

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import psycopg
from sqlalchemy import insert, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from trader_engine.ingest import Candle1m

from trader_db.models import (
    DataImport,
    DataImportError,
    DataProvider,
    ImportConflict,
    RawCandle1m,
)

__all__ = [
    "Candle1m",
    "InsertReport",
    "finish_import",
    "insert_candles",
    "record_error",
    "resolve_conflicts",
    "start_import",
]


@dataclass(frozen=True, slots=True)
class InsertReport:
    inserted: int
    duplicates: int
    conflicts: int


def start_import(
    session: Session,
    *,
    provider_code: str,
    contract_id: int,
    source_type: str,
    source_name: str | None = None,
    params: dict[str, Any] | None = None,
) -> int:
    """Создать импорт 1m в статусе ``running``; вернуть его id."""
    provider_id = session.scalars(
        select(DataProvider.id).where(DataProvider.code == provider_code)
    ).one_or_none()
    if provider_id is None:
        raise LookupError(f"Провайдер {provider_code!r} не найден")
    data_import = DataImport(
        provider_id=provider_id,
        contract_id=contract_id,
        timeframe_code="1m",
        kind="import",
        source_type=source_type,
        source_name=source_name,
        params=params or {},
        status="running",
    )
    session.add(data_import)
    session.flush()
    return data_import.id


def finish_import(
    session: Session,
    import_id: int,
    *,
    status: str = "completed",
    report: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    data_import = _get_running_import(session, import_id)
    data_import.status = status
    data_import.report = report or {}
    data_import.error = error
    data_import.finished_at = datetime.now(UTC)
    session.flush()


def record_error(
    session: Session,
    import_id: int,
    *,
    reason_code: str,
    message: str,
    row_number: int | None = None,
    raw_row: str | None = None,
) -> None:
    """Сохранить отклонённую строку: ошибочные строки не отбрасываются молча."""
    session.add(
        DataImportError(
            data_import_id=import_id,
            row_number=row_number,
            raw_row=raw_row,
            reason_code=reason_code,
            message=message,
        )
    )
    session.flush()


def _get_running_import(session: Session, import_id: int) -> DataImport:
    data_import = session.get(DataImport, import_id)
    if data_import is None:
        raise LookupError(f"Импорт {import_id} не найден")
    if data_import.status != "running":
        raise ValueError(f"Импорт {import_id} уже завершён ({data_import.status})")
    return data_import


def _batches(candles: Sequence[Candle1m], size: int) -> Iterator[Sequence[Candle1m]]:
    for offset in range(0, len(candles), size):
        yield candles[offset : offset + size]


def _same_values(
    row: Any,  # строка результата select с полями свечи
    candle: Candle1m,
) -> bool:
    return (
        row.open == candle.open
        and row.high == candle.high
        and row.low == candle.low
        and row.close == candle.close
        and row.volume == candle.volume
        and row.trade_count == candle.trade_count
        and row.quote_volume == candle.quote_volume
    )


_CANDLE_COLUMNS = (
    "contract_id",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trade_count",
    "quote_volume",
    "data_import_id",
)


def _copy_candles(session: Session, rows: list[dict[str, Any]]) -> None:
    """Массовая вставка через ``COPY`` (примерно в 5 раз быстрее executemany)."""
    session.flush()
    driver = cast(Any, session.connection().connection.driver_connection)
    statement = f"COPY raw_candles_1m ({', '.join(_CANDLE_COLUMNS)}) FROM STDIN"
    try:
        with driver.cursor() as cursor, cursor.copy(statement) as copy:
            for row in rows:
                copy.write_row(tuple(row[column] for column in _CANDLE_COLUMNS))
    except psycopg.Error as error:
        # Как и при обычной вставке: вызывающий ловит исключения SQLAlchemy.
        raise DBAPIError.instance(statement, None, error, psycopg.Error) from error


def insert_candles(
    session: Session,
    import_id: int,
    candles: Sequence[Candle1m],
    *,
    batch_size: int = 5000,
    progress: Callable[[int, int], None] | None = None,
) -> InsertReport:
    """Записать свечи импорта; вернуть, сколько вставлено, продублировано и в конфликте.

    ``progress(сделано, всего)`` вызывается после каждого пакета.

    Свечи должны быть валидными (OHLC, volume) — иначе CHECK в БД остановит запись;
    проверку и учёт ошибочных строк выполняет конвейер импорта.
    """
    data_import = _get_running_import(session, import_id)
    seen: set[datetime] = set()
    for candle in candles:
        if candle.timestamp.tzinfo is None:
            raise ValueError("timestamp свечи должен быть timezone-aware")
        moment = candle.timestamp.astimezone(UTC)
        if moment in seen:
            raise ValueError(f"Повторяющийся timestamp в импорте: {moment}")
        seen.add(moment)

    contract_id = data_import.contract_id
    inserted = duplicates = conflicts = 0
    for batch in _batches(candles, batch_size):
        moments = [c.timestamp.astimezone(UTC) for c in batch]
        active = {
            row.timestamp: row
            for row in session.execute(
                select(
                    RawCandle1m.id,
                    RawCandle1m.timestamp,
                    RawCandle1m.open,
                    RawCandle1m.high,
                    RawCandle1m.low,
                    RawCandle1m.close,
                    RawCandle1m.volume,
                    RawCandle1m.trade_count,
                    RawCandle1m.quote_volume,
                ).where(
                    RawCandle1m.contract_id == contract_id,
                    RawCandle1m.timestamp.in_(moments),
                    RawCandle1m.superseded_by_import_id.is_(None),
                )
            )
        }
        new_rows: list[dict[str, Any]] = []
        conflict_rows: list[dict[str, Any]] = []
        for candle, moment in zip(batch, moments, strict=True):
            existing = active.get(moment)
            if existing is None:
                new_rows.append(
                    {
                        "contract_id": contract_id,
                        "timestamp": moment,
                        "open": candle.open,
                        "high": candle.high,
                        "low": candle.low,
                        "close": candle.close,
                        "volume": candle.volume,
                        "trade_count": candle.trade_count,
                        "quote_volume": candle.quote_volume,
                        "data_import_id": import_id,
                    }
                )
            elif _same_values(existing, candle):
                duplicates += 1
            else:
                conflict_rows.append(
                    {
                        "data_import_id": import_id,
                        "contract_id": contract_id,
                        "timestamp": moment,
                        "existing_candle_id": existing.id,
                        "new_open": candle.open,
                        "new_high": candle.high,
                        "new_low": candle.low,
                        "new_close": candle.close,
                        "new_volume": candle.volume,
                        "new_trade_count": candle.trade_count,
                        "new_quote_volume": candle.quote_volume,
                    }
                )
        if new_rows:
            _copy_candles(session, new_rows)
        if conflict_rows:
            session.execute(insert(ImportConflict), conflict_rows)
        inserted += len(new_rows)
        conflicts += len(conflict_rows)
        if progress is not None:
            progress(inserted + duplicates + conflicts, len(candles))
    return InsertReport(inserted=inserted, duplicates=duplicates, conflicts=conflicts)


def resolve_conflicts(
    session: Session,
    import_id: int,
    *,
    accept: bool,
    conflict_ids: Sequence[int] | None = None,
) -> int | None:
    """Принять или отклонить ожидающие конфликты импорта.

    Принятие создаёт импорт-разрешение и возвращает его id: новые значения
    записываются под ним, а текущие активные строки им замещаются. Если активная
    строка уже равна принимаемой, конфликт закрывается без изменений. Отклонение
    данные не меняет и возвращает ``None``.
    """
    query = select(ImportConflict).where(
        ImportConflict.data_import_id == import_id,
        ImportConflict.status == "pending",
    )
    if conflict_ids is not None:
        query = query.where(ImportConflict.id.in_(conflict_ids))
    conflicts = list(session.scalars(query.order_by(ImportConflict.timestamp)))
    if not conflicts:
        return None

    now = datetime.now(UTC)
    if not accept:
        for conflict in conflicts:
            conflict.status = "rejected"
            conflict.resolved_at = now
        session.flush()
        return None

    origin = session.get(DataImport, import_id)
    assert origin is not None
    resolution = DataImport(
        provider_id=origin.provider_id,
        contract_id=origin.contract_id,
        timeframe_code=origin.timeframe_code,
        kind="conflict_resolution",
        source_type="manual",
        source_name=f"conflicts of import {import_id}",
        resolves_import_id=import_id,
        status="running",
    )
    session.add(resolution)
    session.flush()

    active = {
        row.timestamp: row
        for row in session.execute(
            select(
                RawCandle1m.id,
                RawCandle1m.timestamp,
                RawCandle1m.open,
                RawCandle1m.high,
                RawCandle1m.low,
                RawCandle1m.close,
                RawCandle1m.volume,
                RawCandle1m.trade_count,
                RawCandle1m.quote_volume,
            ).where(
                RawCandle1m.contract_id == origin.contract_id,
                RawCandle1m.timestamp.in_([c.timestamp for c in conflicts]),
                RawCandle1m.superseded_by_import_id.is_(None),
            )
        )
    }
    superseded_ids: list[int] = []
    new_rows: list[dict[str, Any]] = []
    for conflict in conflicts:
        candle = Candle1m(
            timestamp=conflict.timestamp,
            open=conflict.new_open,
            high=conflict.new_high,
            low=conflict.new_low,
            close=conflict.new_close,
            volume=conflict.new_volume,
            trade_count=conflict.new_trade_count,
            quote_volume=conflict.new_quote_volume,
        )
        current = active[conflict.timestamp]
        if not _same_values(current, candle):
            superseded_ids.append(current.id)
            new_rows.append(
                {
                    "contract_id": origin.contract_id,
                    "timestamp": conflict.timestamp,
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                    "trade_count": candle.trade_count,
                    "quote_volume": candle.quote_volume,
                    "data_import_id": resolution.id,
                }
            )
        conflict.status = "accepted"
        conflict.resolved_at = now
        conflict.resolution_import_id = resolution.id

    if superseded_ids:
        # Сначала замещаем активные строки — иначе новые нарушат уникальность активных.
        session.execute(
            update(RawCandle1m)
            .where(RawCandle1m.id.in_(superseded_ids))
            .values(superseded_by_import_id=resolution.id)
            .execution_options(synchronize_session=False)
        )
        session.execute(insert(RawCandle1m), new_rows)
    session.flush()
    finish_import(
        session,
        resolution.id,
        report={"accepted": len(conflicts), "changed": len(new_rows)},
    )
    return resolution.id
