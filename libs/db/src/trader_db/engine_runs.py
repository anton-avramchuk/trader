"""Прогоны движков событий: запуск, инкрементальное продолжение, выборка (ADR-0021).

Прогон детерминирован: ключ — движок, версия алгоритма, отпечаток параметров, ряд
(контракт или continuous-серия root) и таймфрейм. Если бары, обработанные раньше,
не изменились, прогон продолжается на новых барах и результат совпадает с прогоном
«с нуля». Иначе (правка данных, новый ролл continuous, другая версия датасета с
изменившимися барами) создаётся новый прогон, а старые события остаются нетронутыми.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast

from sqlalchemy import Select, insert, select
from sqlalchemy.orm import Session
from trader_engine.events import Event, create, fingerprint, hash_params
from trader_engine.events.base import EventStatus
from trader_engine.indicators import BarInput

from trader_db.models import EngineEvent, EngineRun

RunMode = Literal["auto", "full"]
Outcome = Literal["created", "continued", "unchanged"]
INSERT_CHUNK = 2000


@dataclass(frozen=True, slots=True)
class RunOutcome:
    run_id: int
    outcome: Outcome
    bars_total: int
    bars_new: int
    events_written: int
    # Почему начат новый прогон вместо продолжения (для ``created``).
    reason: str | None = None


def find_latest_run(
    session: Session,
    engine: str,
    params_hash: str,
    algorithm_version: int,
    timeframe: str,
    *,
    contract_id: int | None = None,
    root_id: int | None = None,
    lock: bool = False,
) -> EngineRun | None:
    query = (
        select(EngineRun)
        .where(
            EngineRun.engine == engine,
            EngineRun.params_hash == params_hash,
            EngineRun.algorithm_version == algorithm_version,
            EngineRun.timeframe_code == timeframe,
            EngineRun.contract_id == contract_id
            if contract_id is not None
            else EngineRun.root_id == root_id,
        )
        .order_by(EngineRun.id.desc())
        .limit(1)
    )
    if lock:
        query = query.with_for_update()
    return session.scalars(query).one_or_none()


def _row(run_id: int, event: Event, dataset_version_id: int | None) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "seq": event.seq,
        "kind": event.kind,
        "status": event.status,
        "payload": event.payload,
        "detected_at": event.detected_at,
        "confirmed_at": event.confirmed_at,
        "available_at": event.available_at,
        "revises_seq": event.revises,
        "dataset_version_id": dataset_version_id,
    }


def _resumable(run: EngineRun, bars: Sequence[BarInput]) -> str | None:
    """``None``, если прогон можно продолжить на ``bars``; иначе причина отказа."""
    processed = run.bars_processed
    if processed == 0 or run.last_close_time is None:
        return "в предыдущем прогоне нет обработанных баров"
    if processed > len(bars):
        return "ряд стал короче обработанной части"
    head = bars[:processed]
    if head[-1].close_time != run.last_close_time:
        return "сетка баров изменилась"
    if fingerprint(head) != run.input_fingerprint:
        return "ранее обработанные бары изменились"
    return None


def advance_run(
    session: Session,
    engine_name: str,
    params: dict[str, Any] | None,
    bars: Sequence[BarInput],
    timeframe: str,
    *,
    contract_id: int | None = None,
    root_id: int | None = None,
    dataset_version_id: int | None = None,
    mode: RunMode = "auto",
) -> RunOutcome:
    """Довести прогон до конца ``bars``: продолжить прежний или начать новый.

    ``bars`` — полный ряд от начала истории (отпечаток сверяет обработанную часть).
    ``mode="full"`` всегда начинает новый прогон.
    """
    if (contract_id is None) == (root_id is None):
        raise ValueError("Укажите ровно один из: contract_id или root_id")
    if not bars:
        raise ValueError("Нет баров для прогона")
    engine = create(engine_name, params)
    phash = hash_params(engine.params)
    previous = (
        None
        if mode == "full"
        else find_latest_run(
            session,
            engine.name,
            phash,
            engine.version,
            timeframe,
            contract_id=contract_id,
            root_id=root_id,
            lock=True,
        )
    )
    if mode == "full":
        reason: str | None = "запрошен полный прогон"
    elif previous is None:
        reason = "прогона с такими параметрами ещё нет"
    else:
        reason = _resumable(previous, bars)

    if previous is not None and reason is None:
        run = previous
        engine.load_state(run.state)
        new_bars = list(bars[run.bars_processed :])
        seed = run.input_fingerprint
        outcome: Outcome = "continued"
        if not new_bars:
            return RunOutcome(run.id, "unchanged", len(bars), 0, 0)
    else:
        run = EngineRun(
            engine=engine.name,
            algorithm_version=engine.version,
            params=engine.params.model_dump(mode="json"),
            params_hash=phash,
            contract_id=contract_id,
            root_id=root_id,
            timeframe_code=timeframe,
            dataset_version_id=dataset_version_id,
            bars_processed=0,
            state={},
        )
        session.add(run)
        session.flush()
        new_bars = list(bars)
        seed = ""
        outcome = "created"

    events: list[Event] = []
    for bar in new_bars:
        events.extend(engine.update(bar))
    for start in range(0, len(events), INSERT_CHUNK):
        chunk = events[start : start + INSERT_CHUNK]
        session.execute(
            insert(EngineEvent), [_row(run.id, e, dataset_version_id) for e in chunk]
        )

    run.state = engine.dump_state()
    run.bars_processed += len(new_bars)
    run.last_close_time = new_bars[-1].close_time
    run.input_fingerprint = fingerprint(new_bars, seed)
    run.dataset_version_id = dataset_version_id
    session.flush()
    return RunOutcome(
        run.id,
        outcome,
        len(bars),
        len(new_bars),
        len(events),
        reason if outcome == "created" else None,
    )


def events_statement(
    run_id: int,
    *,
    as_of: datetime | None = None,
    kinds: Sequence[str] | None = None,
    after_seq: int | None = None,
) -> Select[EngineEvent]:
    """Запрос событий прогона по возрастанию ``seq`` (``available_at <= as_of``)."""
    query = select(EngineEvent).where(EngineEvent.run_id == run_id)
    if as_of is not None:
        query = query.where(EngineEvent.available_at <= as_of)
    if kinds:
        query = query.where(EngineEvent.kind.in_(kinds))
    if after_seq is not None:
        query = query.where(EngineEvent.seq > after_seq)
    return query.order_by(EngineEvent.seq)


def to_event(row: EngineEvent) -> Event:
    return Event(
        seq=row.seq,
        kind=row.kind,
        status=cast(EventStatus, row.status),
        payload=row.payload,
        detected_at=row.detected_at,
        confirmed_at=row.confirmed_at,
        available_at=row.available_at,
        revises=row.revises_seq,
    )


def load_events(
    session: Session,
    run_id: int,
    *,
    as_of: datetime | None = None,
    kinds: Sequence[str] | None = None,
) -> list[Event]:
    """События прогона, известные к ``as_of`` (без ``as_of`` — все)."""
    rows = session.scalars(events_statement(run_id, as_of=as_of, kinds=kinds))
    return [to_event(row) for row in rows]
