"""Бэктесты: эксперименты, сделки, окна, журнал запусков и блокировка test (ADR-0027).

Test-период связки (root, TF, семейство стратегии) открывается один раз:
``open_test_period`` создаёт блокировку, повторная попытка отклоняется и пишется
в журнал. Разблокировка — только явная (``unlock_test_period``), тоже в журнал.
Журнал хранит все запуски, включая неудачные, — по нему видно, сколько вариантов
перебрано до результата.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trader_db.models import (
    BacktestExperiment,
    BacktestLog,
    BacktestTestLock,
    BacktestTrade,
    BacktestWindow,
)

ERROR_LIMIT = 2000


def log_event(
    session: Session,
    event: str,
    *,
    instrument_id: int | None = None,
    timeframe_code: str | None = None,
    family: str | None = None,
    experiment_id: int | None = None,
    params_hash: str | None = None,
    period_from: date | None = None,
    period_to: date | None = None,
    touches_test: bool = False,
    note: str | None = None,
) -> BacktestLog:
    entry = BacktestLog(
        event=event,
        instrument_id=instrument_id,
        timeframe_code=timeframe_code,
        family=family,
        experiment_id=experiment_id,
        params_hash=params_hash,
        period_from=period_from,
        period_to=period_to,
        touches_test=touches_test,
        note=note,
    )
    session.add(entry)
    session.flush()
    return entry


def create_experiment(
    session: Session,
    *,
    kind: str,
    instrument_id: int,
    timeframe_code: str,
    family: str,
    strategy: dict[str, Any],
    costs: dict[str, Any],
    period_from: date,
    period_to: date,
    params_hash: str,
    quantity: int = 1,
    test_from: date | None = None,
    test_to: date | None = None,
    walk_forward: dict[str, Any] | None = None,
    versions: dict[str, Any] | None = None,
    job_id: int | None = None,
) -> BacktestExperiment:
    """Новый эксперимент (``queued``) и запись о запуске в журнале."""
    experiment = BacktestExperiment(
        kind=kind,
        instrument_id=instrument_id,
        timeframe_code=timeframe_code,
        family=family,
        strategy=strategy,
        costs=costs,
        quantity=quantity,
        period_from=period_from,
        period_to=period_to,
        test_from=test_from,
        test_to=test_to,
        walk_forward=walk_forward,
        versions=versions or {},
        params_hash=params_hash,
        job_id=job_id,
    )
    session.add(experiment)
    session.flush()
    log_event(
        session,
        "walk_forward" if kind == "walk_forward" else "run",
        instrument_id=instrument_id,
        timeframe_code=timeframe_code,
        family=family,
        experiment_id=experiment.id,
        params_hash=params_hash,
        period_from=period_from,
        period_to=period_to,
        touches_test=test_from is not None,
    )
    return experiment


def mark_running(session: Session, experiment_id: int) -> None:
    experiment = session.get_one(BacktestExperiment, experiment_id)
    experiment.status = "running"
    session.flush()


def finish_experiment(
    session: Session, experiment_id: int, result: dict[str, Any]
) -> BacktestExperiment:
    experiment = session.get_one(BacktestExperiment, experiment_id)
    experiment.status = "succeeded"
    experiment.result = result
    experiment.finished_at = datetime.now(UTC)
    session.flush()
    return experiment


def fail_experiment(
    session: Session, experiment_id: int, error: str
) -> BacktestExperiment:
    experiment = session.get_one(BacktestExperiment, experiment_id)
    experiment.status = "failed"
    experiment.error = error[:ERROR_LIMIT]
    experiment.finished_at = datetime.now(UTC)
    session.flush()
    log_event(
        session,
        "failed",
        instrument_id=experiment.instrument_id,
        timeframe_code=experiment.timeframe_code,
        family=experiment.family,
        experiment_id=experiment_id,
        params_hash=experiment.params_hash,
        note=error[:500],
    )
    return experiment


def add_window(
    session: Session,
    experiment_id: int,
    sequence: int,
    *,
    train: tuple[date, date],
    valid: tuple[date, date],
    params: dict[str, Any],
    train_metrics: dict[str, Any] | None = None,
    valid_metrics: dict[str, Any] | None = None,
) -> BacktestWindow:
    window = BacktestWindow(
        experiment_id=experiment_id,
        sequence=sequence,
        train_from=train[0],
        train_to=train[1],
        valid_from=valid[0],
        valid_to=valid[1],
        params=params,
        train_metrics=train_metrics,
        valid_metrics=valid_metrics,
    )
    session.add(window)
    session.flush()
    return window


def add_trades(
    session: Session,
    experiment_id: int,
    rows: Sequence[dict[str, Any]],
    *,
    segment: str = "single",
    window_id: int | None = None,
) -> int:
    """Сохраняет сделки; ``sequence`` — сквозной номер по эксперименту и сегменту.

    Сделки нескольких окон одного сегмента (validation) нумеруются подряд, поэтому
    повторные вызовы продолжают нумерацию, а не начинают её с нуля.
    """
    last = session.scalar(
        select(func.max(BacktestTrade.sequence)).where(
            BacktestTrade.experiment_id == experiment_id,
            BacktestTrade.segment == segment,
        )
    )
    start = 0 if last is None else last + 1
    for offset, row in enumerate(rows):
        session.add(
            BacktestTrade(
                experiment_id=experiment_id,
                window_id=window_id,
                segment=segment,
                sequence=start + offset,
                **row,
            )
        )
    session.flush()
    return len(rows)


def list_trades(
    session: Session,
    experiment_id: int,
    *,
    segment: str | None = None,
    window_id: int | None = None,
) -> list[BacktestTrade]:
    query = select(BacktestTrade).where(BacktestTrade.experiment_id == experiment_id)
    if segment is not None:
        query = query.where(BacktestTrade.segment == segment)
    if window_id is not None:
        query = query.where(BacktestTrade.window_id == window_id)
    return list(
        session.scalars(
            query.order_by(
                BacktestTrade.segment, BacktestTrade.window_id, BacktestTrade.sequence
            )
        )
    )


def list_windows(session: Session, experiment_id: int) -> list[BacktestWindow]:
    return list(
        session.scalars(
            select(BacktestWindow)
            .where(BacktestWindow.experiment_id == experiment_id)
            .order_by(BacktestWindow.sequence)
        )
    )


@dataclass(frozen=True, slots=True)
class LockAttempt:
    """Итог попытки открыть test: ``opened`` — открыт сейчас; иначе уже был открыт."""

    opened: bool
    lock: BacktestTestLock


def open_test_period(
    session: Session,
    *,
    experiment_id: int,
    instrument_id: int,
    timeframe_code: str,
    family: str,
    test_from: date,
    test_to: date,
) -> LockAttempt:
    """Открывает test один раз; повторная попытка отклоняется и пишется в журнал."""
    existing = session.scalars(
        select(BacktestTestLock).where(
            BacktestTestLock.instrument_id == instrument_id,
            BacktestTestLock.timeframe_code == timeframe_code,
            BacktestTestLock.family == family,
        )
    ).one_or_none()
    common: dict[str, Any] = {
        "instrument_id": instrument_id,
        "timeframe_code": timeframe_code,
        "family": family,
        "experiment_id": experiment_id,
        "period_from": test_from,
        "period_to": test_to,
        "touches_test": True,
    }
    if existing is not None:
        log_event(
            session,
            "test_rejected",
            note=(
                f"test {existing.test_from}…{existing.test_to} уже открыт "
                f"экспериментом {existing.experiment_id}"
            ),
            **common,
        )
        return LockAttempt(False, existing)
    lock = BacktestTestLock(
        instrument_id=instrument_id,
        timeframe_code=timeframe_code,
        family=family,
        test_from=test_from,
        test_to=test_to,
        experiment_id=experiment_id,
    )
    session.add(lock)
    session.flush()
    log_event(session, "test_opened", **common)
    return LockAttempt(True, lock)


def unlock_test_period(
    session: Session,
    *,
    instrument_id: int,
    timeframe_code: str,
    family: str,
    note: str,
) -> bool:
    """Явная разблокировка: блокировка снимается, причина — в журнале."""
    lock = session.scalars(
        select(BacktestTestLock).where(
            BacktestTestLock.instrument_id == instrument_id,
            BacktestTestLock.timeframe_code == timeframe_code,
            BacktestTestLock.family == family,
        )
    ).one_or_none()
    if lock is None:
        return False
    log_event(
        session,
        "unlock",
        instrument_id=instrument_id,
        timeframe_code=timeframe_code,
        family=family,
        experiment_id=lock.experiment_id,
        period_from=lock.test_from,
        period_to=lock.test_to,
        touches_test=True,
        note=note,
    )
    session.delete(lock)
    session.flush()
    return True


def count_runs(
    session: Session, *, instrument_id: int, timeframe_code: str, family: str
) -> int:
    """Сколько запусков (включая неудачные) уже было по связке — для оценки перебора."""
    return int(
        session.scalar(
            select(func.count())
            .select_from(BacktestLog)
            .where(
                BacktestLog.instrument_id == instrument_id,
                BacktestLog.timeframe_code == timeframe_code,
                BacktestLog.family == family,
                BacktestLog.event.in_(["run", "walk_forward"]),
            )
        )
        or 0
    )


def list_log(
    session: Session,
    *,
    instrument_id: int | None = None,
    timeframe_code: str | None = None,
    family: str | None = None,
    limit: int = 200,
) -> list[BacktestLog]:
    query = select(BacktestLog)
    if instrument_id is not None:
        query = query.where(BacktestLog.instrument_id == instrument_id)
    if timeframe_code is not None:
        query = query.where(BacktestLog.timeframe_code == timeframe_code)
    if family is not None:
        query = query.where(BacktestLog.family == family)
    return list(session.scalars(query.order_by(BacktestLog.id.desc()).limit(limit)))
