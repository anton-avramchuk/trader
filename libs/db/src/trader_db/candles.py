"""Свечи инструментов: запись из importer и чтение для графика и движков (ADR-0028).

Загрузка за период перезаписывает свечи (upsert по ``(instrument, TF, open_time)``);
журнал ``candle_loads`` хранит, что и за какой период загружено. Версий датасета
нет: если история изменилась, прогоны движков замечают это по отпечатку баров.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from trader_engine.bars import Bar

from trader_db.models import Candle, CandleLoad

INSERT_CHUNK = 2000


@dataclass(frozen=True, slots=True)
class CandleRow:
    """Свеча для записи: что приходит от importer, плюс вычисленные поля."""

    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trading_day: date


def upsert_candles(
    session: Session, instrument_id: int, timeframe: str, rows: Sequence[CandleRow]
) -> int:
    """Записывает свечи, заменяя существующие с тем же временем открытия."""
    written = 0
    for offset in range(0, len(rows), INSERT_CHUNK):
        chunk = rows[offset : offset + INSERT_CHUNK]
        values: list[dict[str, Any]] = [
            {
                "instrument_id": instrument_id,
                "timeframe_code": timeframe,
                "open_time": r.open_time,
                "close_time": r.close_time,
                "open": r.open,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "volume": r.volume,
                "trading_day": r.trading_day,
            }
            for r in chunk
        ]
        statement = insert(Candle).values(values)
        session.execute(
            statement.on_conflict_do_update(
                index_elements=["instrument_id", "timeframe_code", "open_time"],
                set_={
                    "close_time": statement.excluded.close_time,
                    "open": statement.excluded.open,
                    "high": statement.excluded.high,
                    "low": statement.excluded.low,
                    "close": statement.excluded.close,
                    "volume": statement.excluded.volume,
                    "trading_day": statement.excluded.trading_day,
                },
            )
        )
        written += len(chunk)
    return written


def record_load(
    session: Session,
    *,
    instrument_id: int,
    timeframe: str,
    period_from: datetime,
    period_to: datetime,
    rows: int,
    source: str,
    job_id: int | None = None,
) -> CandleLoad:
    load = CandleLoad(
        instrument_id=instrument_id,
        timeframe_code=timeframe,
        period_from=period_from,
        period_to=period_to,
        rows=rows,
        source=source,
        job_id=job_id,
    )
    session.add(load)
    session.flush()
    return load


def read_bars(
    session: Session,
    instrument_id: int,
    timeframe: str,
    start: datetime | None = None,
    end: datetime | None = None,
    *,
    closed_until: datetime | None = None,
    limit: int | None = None,
    tail: bool = False,
) -> list[Bar]:
    """Свечи по возрастанию времени; ``[start, end)`` — фильтр по открытию.

    ``closed_until`` оставляет только свечи, закрытые к этому моменту
    (``close_time <= closed_until``) — срез знания без заглядывания в будущее.
    ``limit`` ограничивает число свечей: с начала диапазона, а при ``tail`` —
    последние ``limit`` (порядок всё равно по возрастанию).
    """
    query = select(Candle).where(
        Candle.instrument_id == instrument_id, Candle.timeframe_code == timeframe
    )
    if start is not None:
        query = query.where(Candle.open_time >= start)
    if end is not None:
        query = query.where(Candle.open_time < end)
    if closed_until is not None:
        query = query.where(Candle.close_time <= closed_until)
    query = query.order_by(Candle.open_time.desc() if tail else Candle.open_time)
    if limit is not None:
        query = query.limit(limit)
    rows = list(session.scalars(query))
    if tail:
        rows.reverse()
    return [
        Bar(
            timeframe=row.timeframe_code,
            timestamp=row.open_time,
            close_time=row.close_time,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
            trading_day=row.trading_day,
        )
        for row in rows
    ]


def last_open_time(
    session: Session, instrument_id: int, timeframe: str
) -> datetime | None:
    """Время открытия последней загруженной свечи (для докачки)."""
    return session.scalar(
        select(func.max(Candle.open_time)).where(
            Candle.instrument_id == instrument_id, Candle.timeframe_code == timeframe
        )
    )


def count_candles(session: Session, instrument_id: int, timeframe: str) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(Candle)
            .where(
                Candle.instrument_id == instrument_id,
                Candle.timeframe_code == timeframe,
            )
        )
        or 0
    )
