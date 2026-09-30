"""Построение баров TF из минутных свечей и чтение их из БД (ADR-0018).

Сборка потоковая: свечи версии датасета читаются пачками, бары пишутся ``COPY``.
Пересборка **инкрементальная**: от торговой недели, в которой появились новые
минутные свечи (или которой принадлежит последняя свеча, потому что её бар мог
быть не закрыт). Полная пересборка — если изменился календарь (отпечаток),
флаг выходных сессий, версия алгоритма или запрошена ``force``.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from sqlalchemy import CursorResult, and_, delete, exists, func, select
from sqlalchemy.orm import Session
from trader_engine.aggregation import Bar, BarAggregator
from trader_engine.calendar import TradingCalendar
from trader_engine.ingest import Candle1m

from trader_db.bulk import copy_rows
from trader_db.datasets import latest_dataset_version, manifest_of, visible_filter
from trader_db.jobs import ACTIVE_STATUSES, enqueue_job
from trader_db.models import DerivedBuild, DerivedCandle, Job, RawCandle1m

# Смена версии алгоритма агрегации приводит к полной пересборке.
ALGORITHM_VERSION = 1
AGGREGATE_JOB_TYPE = "aggregate.contract"
_FETCH_SIZE = 10_000
_INSERT_BATCH = 20_000

_BAR_COLUMNS = (
    "contract_id",
    "timeframe_code",
    "timestamp",
    "close_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trade_count",
    "candles",
    "is_partial",
    "trading_day",
    "built_from_version_id",
)


@dataclass(frozen=True, slots=True)
class BuildResult:
    timeframe: str
    # ``full``, ``incremental`` или ``noop`` (ничего не изменилось).
    mode: str
    rows_deleted: int = 0
    rows_written: int = 0
    rebuilt_from_day: date | None = None
    report: dict[str, Any] = field(default_factory=dict[str, Any])


def stream_candles(
    session: Session,
    dataset_version_id: int,
    contract_id: int,
    *,
    start: datetime | None = None,
) -> Iterator[Candle1m]:
    """Минутные свечи версии по возрастанию времени, пачками (без загрузки в память)."""
    manifest = manifest_of(session, dataset_version_id)
    query = select(RawCandle1m).where(*visible_filter(contract_id, manifest))
    if start is not None:
        query = query.where(RawCandle1m.timestamp >= start)
    query = query.order_by(RawCandle1m.timestamp).execution_options(
        yield_per=_FETCH_SIZE
    )
    for row in session.scalars(query):
        yield Candle1m(
            timestamp=row.timestamp,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
            trade_count=row.trade_count,
            quote_volume=row.quote_volume,
        )


def _week_of(calendar: TradingCalendar, moment: datetime) -> date:
    """Начало торговой недели, к которой относится момент времени."""
    session = calendar.session_at(moment)
    day = (
        session.trading_day
        if session is not None
        else moment.astimezone(ZoneInfo(calendar.timezone)).date()
    )
    return calendar.trading_week_start(day)


def _new_candle_range(
    session: Session, contract_id: int, latest_id: int, previous_id: int
) -> datetime | None:
    """Самое раннее время свечи, появившейся в версии ``latest`` после ``previous``."""
    new_imports = set(manifest_of(session, latest_id)) - set(
        manifest_of(session, previous_id)
    )
    if not new_imports:
        return None
    return session.scalar(
        select(func.min(RawCandle1m.timestamp)).where(
            RawCandle1m.contract_id == contract_id,
            RawCandle1m.data_import_id.in_(new_imports),
        )
    )


def _last_candle(
    session: Session, contract_id: int, version_id: int
) -> datetime | None:
    manifest = manifest_of(session, version_id)
    return session.scalar(
        select(func.max(RawCandle1m.timestamp)).where(
            and_(*visible_filter(contract_id, manifest))
        )
    )


def build_bars(
    session: Session,
    contract_id: int,
    timeframe: str,
    calendar: TradingCalendar,
    *,
    include_weekend_sessions: bool,
    complete_until: datetime,
    force: bool = False,
) -> BuildResult:
    """Построить (или достроить) бары ``timeframe`` контракта из последней версии 1m.

    ``complete_until`` — момент, до которого данные заведомо полны: бар, закрытие
    которого позже, не публикуется (формирующийся бар в расчёты не попадает).
    Транзакцию фиксирует вызывающий.
    """
    latest = latest_dataset_version(session, contract_id, "1m")
    if latest is None:
        raise LookupError(f"У контракта {contract_id} нет минутных данных")
    effective = (
        calendar if include_weekend_sessions else calendar.excluding_weekend_sessions()
    )
    fingerprint = effective.fingerprint()
    previous = session.scalars(
        select(DerivedBuild)
        .where(
            DerivedBuild.contract_id == contract_id,
            DerivedBuild.timeframe_code == timeframe,
        )
        .order_by(DerivedBuild.id.desc())
        .limit(1)
    ).one_or_none()

    rebuilt_from_day: date | None = None
    start: datetime | None = None
    mode = "full"
    if (
        not force
        and previous is not None
        and previous.calendar_hash == fingerprint
        and previous.include_weekend_sessions == include_weekend_sessions
        and previous.algorithm_version == ALGORITHM_VERSION
    ):
        if (
            previous.source_dataset_version_id == latest.id
            and complete_until <= previous.complete_until
        ):
            return BuildResult(timeframe, "noop")
        mode = "incremental"
        last_moment = _last_candle(session, contract_id, latest.id)
        changed = _new_candle_range(
            session, contract_id, latest.id, previous.source_dataset_version_id
        )
        anchors = [m for m in (changed, last_moment) if m is not None]
        if anchors:
            rebuilt_from_day = _week_of(effective, min(anchors))
            bounds = effective.trading_week_bounds(rebuilt_from_day)
            start = bounds[0] if bounds is not None else None
    if mode == "full" or start is None:
        mode, rebuilt_from_day, start = "full", None, None

    delete_query = delete(DerivedCandle).where(
        DerivedCandle.contract_id == contract_id,
        DerivedCandle.timeframe_code == timeframe,
    )
    if rebuilt_from_day is not None:
        delete_query = delete_query.where(DerivedCandle.trading_day >= rebuilt_from_day)
    deleted = cast(CursorResult[Any], session.execute(delete_query))
    rows_deleted = deleted.rowcount or 0

    aggregator = BarAggregator(effective, timeframe, complete_until=complete_until)
    written = 0
    batch: list[Bar] = []

    def flush() -> None:
        nonlocal written
        if batch:
            copy_rows(
                session,
                "derived_candles",
                _BAR_COLUMNS,
                (
                    (
                        contract_id,
                        timeframe,
                        bar.timestamp,
                        bar.close_time,
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.volume,
                        bar.trade_count,
                        bar.candles,
                        bar.is_partial,
                        bar.trading_day,
                        latest.id,
                    )
                    for bar in batch
                ),
            )
            written += len(batch)
            batch.clear()

    for candle in stream_candles(session, latest.id, contract_id, start=start):
        batch.extend(aggregator.push(candle))
        if len(batch) >= _INSERT_BATCH:
            flush()
    batch.extend(aggregator.finish())
    flush()

    stats = aggregator.stats
    report: dict[str, Any] = {
        "candles_in": stats.candles_in,
        "bars_out": stats.bars_out,
        "skipped_outside_session": stats.skipped_outside_session,
        "skipped_sample": [m.isoformat() for m in stats.skipped_sample[:20]],
        "last_bar_withheld": stats.last_bar_withheld,
    }
    session.add(
        DerivedBuild(
            contract_id=contract_id,
            timeframe_code=timeframe,
            source_dataset_version_id=latest.id,
            calendar_hash=fingerprint,
            include_weekend_sessions=include_weekend_sessions,
            algorithm_version=ALGORITHM_VERSION,
            mode=mode,
            rebuilt_from_day=rebuilt_from_day,
            complete_until=complete_until,
            rows_deleted=rows_deleted,
            rows_written=written,
            report=report,
        )
    )
    session.flush()
    return BuildResult(timeframe, mode, rows_deleted, written, rebuilt_from_day, report)


def read_bars(
    session: Session,
    contract_id: int,
    timeframe: str,
    start: datetime | None = None,
    end: datetime | None = None,
    *,
    closed_until: datetime | None = None,
    closes_after: datetime | None = None,
    limit: int | None = None,
    tail: bool = False,
) -> list[Bar]:
    """Бары контракта по возрастанию времени; ``[start, end)`` — фильтр.

    ``closed_until`` оставляет только бары, закрытые к этому моменту
    (``close_time <= closed_until``), ``closes_after`` — только закрывающиеся позже
    этого момента (``close_time > closes_after``). ``limit`` ограничивает число баров: с начала
    диапазона, а при ``tail`` — последние ``limit`` (порядок всё равно по возрастанию).
    """
    query = select(DerivedCandle).where(
        DerivedCandle.contract_id == contract_id,
        DerivedCandle.timeframe_code == timeframe,
    )
    if start is not None:
        query = query.where(DerivedCandle.timestamp >= start)
    if end is not None:
        query = query.where(DerivedCandle.timestamp < end)
    if closed_until is not None:
        query = query.where(DerivedCandle.close_time <= closed_until)
    if closes_after is not None:
        query = query.where(DerivedCandle.close_time > closes_after)
    query = query.order_by(
        DerivedCandle.timestamp.desc() if tail else DerivedCandle.timestamp
    )
    if limit is not None:
        query = query.limit(limit)
    rows = list(session.scalars(query))
    if tail:
        rows.reverse()
    return [
        Bar(
            timeframe=row.timeframe_code,
            timestamp=row.timestamp,
            close_time=row.close_time,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
            trade_count=row.trade_count,
            candles=row.candles,
            is_partial=row.is_partial,
            trading_day=row.trading_day,
        )
        for row in rows
    ]


def enqueue_aggregation(session: Session, contract_id: int) -> int | None:
    """Поставить сборку баров контракта; ``None``, если такая задача уже ждёт."""
    pending = session.scalar(
        select(
            exists().where(
                Job.type == AGGREGATE_JOB_TYPE,
                Job.params["contract_id"].as_integer() == contract_id,
                Job.status.in_(ACTIVE_STATUSES),
            )
        )
    )
    if pending:
        return None
    return enqueue_job(session, AGGREGATE_JOB_TYPE, {"contract_id": contract_id})
