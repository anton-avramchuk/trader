"""Задача ``candles.load``: загрузка свечей инструмента из importer (ADR-0028).

Параметры: ``instrument_id``; ``period_from`` и ``period_to`` (ISO-даты или момент
времени с зоной; границы ``[from, to)``); необязательные ``timeframes`` (по
умолчанию все). Свечи приходят готовыми и закрытыми и записываются поверх
существующих (upsert); по каждому таймфрейму пишется строка журнала загрузок.
Страницы фиксируются по мере получения, поэтому отмена не теряет уже загруженное.
"""

from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker
from trader_db import CandleRow, get_instrument, record_load, upsert_candles
from trader_engine.timeframes import TIMEFRAMES

from trader_worker.handlers import Handler, JobContext, JobFailed
from trader_worker.importer_client import ImporterClient, ImporterError

CANDLES_LOAD_JOB_TYPE = "candles.load"
SOURCE = "importer"


def parse_moment(value: Any, zone: ZoneInfo) -> datetime:
    """Дата (начало дня в зоне инструмента) или момент с зоной → UTC."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime.combine(value, time.min, tzinfo=zone)
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise JobFailed(f"Неверная дата или время: {value!r}") from error
        if "T" not in value and " " not in value:  # только дата
            moment = datetime.combine(moment.date(), time.min, tzinfo=zone)
    else:
        raise JobFailed(f"Неверная дата или время: {value!r}")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=zone)
    return moment.astimezone(UTC)


def make_candles_load_handler(
    session_factory: sessionmaker[Session],
    client_factory: type[ImporterClient] | Any = ImporterClient,
) -> Handler:
    def handler(context: JobContext) -> dict[str, Any]:
        params = context.params
        instrument_id = params.get("instrument_id")
        if not isinstance(instrument_id, int):
            raise JobFailed("Укажите instrument_id")
        timeframes = params.get("timeframes") or list(TIMEFRAMES)
        unknown = [tf for tf in timeframes if tf not in TIMEFRAMES]
        if unknown:
            raise JobFailed(
                f"Неизвестные таймфреймы {unknown}; доступны: {', '.join(TIMEFRAMES)}"
            )
        with session_factory() as session:
            instrument = get_instrument(session, instrument_id)
            if instrument is None:
                raise JobFailed(f"Инструмент {instrument_id} не найден")
            ticker, zone_name = instrument.ticker, instrument.timezone
        zone = ZoneInfo(zone_name)
        start = parse_moment(params.get("period_from"), zone)
        end = parse_moment(params.get("period_to"), zone)
        if start >= end:
            raise JobFailed("period_from должен быть раньше period_to")

        client = client_factory()
        loaded: dict[str, int] = {}
        try:
            for position, timeframe in enumerate(timeframes):
                loaded[timeframe] = _load_timeframe(
                    session_factory,
                    client,
                    context,
                    instrument_id,
                    ticker,
                    zone,
                    timeframe,
                    start,
                    end,
                    (position, len(timeframes)),
                )
        except ImporterError as error:
            raise JobFailed(str(error)) from error
        finally:
            client.close()
        context.report_progress(1.0, "готово")
        return {
            "instrument_id": instrument_id,
            "ticker": ticker,
            "period_from": start.isoformat(),
            "period_to": end.isoformat(),
            "loaded": loaded,
        }

    return handler


def _load_timeframe(
    session_factory: sessionmaker[Session],
    client: ImporterClient,
    context: JobContext,
    instrument_id: int,
    ticker: str,
    zone: ZoneInfo,
    timeframe: str,
    start: datetime,
    end: datetime,
    place: tuple[int, int],
) -> int:
    length = TIMEFRAMES[timeframe]
    total = 0
    span = (end - start).total_seconds()
    for candles in client.pages(ticker, timeframe, start, end):
        rows = [
            CandleRow(
                open_time=c.t,
                close_time=c.t + length,
                open=c.o,
                high=c.h,
                low=c.l,
                close=c.c,
                volume=c.v,
                trading_day=c.t.astimezone(zone).date(),
            )
            for c in candles
        ]
        with session_factory() as session, session.begin():
            total += upsert_candles(session, instrument_id, timeframe, rows)
        covered = min(1.0, (candles[-1].t - start).total_seconds() / span)
        context.report_progress(
            (place[0] + covered) / place[1], f"{ticker} {timeframe}: {total} свечей"
        )
    with session_factory() as session, session.begin():
        record_load(
            session,
            instrument_id=instrument_id,
            timeframe=timeframe,
            period_from=start,
            period_to=end,
            rows=total,
            source=SOURCE,
            job_id=context.job_id,
        )
    return total
