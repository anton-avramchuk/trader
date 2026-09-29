"""Задача ``aggregate.contract``: сборка баров 15m/1h/4h/1d/1w из минутных свечей.

Параметры: ``contract_id``; необязательные ``timeframes`` (по умолчанию все) и
``force`` (полная пересборка). Пересборка инкрементальная (ADR-0018): неизменившиеся
данные не трогаются. Каждый таймфрейм собирается в своей транзакции.
"""

from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from trader_db import (
    AGGREGATE_JOB_TYPE,
    build_bars,
    load_contract_calendar,
    loaded_windows,
)
from trader_db.models import Contract, RawCandle1m, Root
from trader_engine.aggregation import TIMEFRAMES

from trader_worker.handlers import Handler, JobContext, JobFailed
from trader_worker.iss_jobs import MSK, PROVIDER_CODE

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def _complete_until(
    session: Session, contract_id: int, now: datetime
) -> datetime | None:
    """Момент, до которого минутные данные контракта заведомо полны.

    Не раньше конца последней свечи; если ISS загружен окном дат, то до конца
    последнего дня окна (ISS не отдаёт минуты без сделок, так что по последней
    свече конец данных не определить). Не позже ``now``.
    """
    last = session.scalar(
        select(func.max(RawCandle1m.timestamp)).where(
            RawCandle1m.contract_id == contract_id
        )
    )
    if last is None:
        return None
    known = last + timedelta(minutes=1)
    windows = loaded_windows(session, contract_id, PROVIDER_CODE)
    if windows:
        last_day = max(till for _, till in windows)
        end_of_day = datetime.combine(
            last_day + timedelta(days=1), time(0), tzinfo=MSK
        ).astimezone(UTC)
        known = max(known, end_of_day)
    return min(known, now)


def make_aggregate_handler(
    session_factory: sessionmaker[Session], *, now: Clock = utc_now
) -> Handler:
    def run(context: JobContext) -> dict[str, Any]:
        params = context.params
        contract_id = params.get("contract_id")
        if isinstance(contract_id, bool) or not isinstance(contract_id, int):
            raise JobFailed("Не задан параметр contract_id (целое число)")
        requested = params.get("timeframes") or list(TIMEFRAMES)
        unknown = [code for code in requested if code not in TIMEFRAMES]
        if unknown:
            raise JobFailed(f"Неизвестные таймфреймы: {', '.join(map(str, unknown))}")
        force = bool(params.get("force", False))

        with session_factory() as session:
            contract = session.get(Contract, contract_id)
            if contract is None:
                raise JobFailed(f"Контракт {contract_id} не найден")
            root = session.get(Root, contract.root_id)
            assert root is not None
            include_weekend = root.include_weekend_sessions
            calendar = load_contract_calendar(session, contract_id)
            complete_until = _complete_until(session, contract_id, now())
        if complete_until is None:
            raise JobFailed(f"У контракта {contract_id} нет минутных данных")

        results: list[dict[str, Any]] = []
        for index, timeframe in enumerate(requested):
            context.report_progress(index / len(requested), f"сборка {timeframe}")
            with session_factory() as session:
                result = build_bars(
                    session,
                    contract_id,
                    timeframe,
                    calendar,
                    include_weekend_sessions=include_weekend,
                    complete_until=complete_until,
                    force=force,
                )
                session.commit()
            results.append(
                {
                    "timeframe": timeframe,
                    "mode": result.mode,
                    "rows_deleted": result.rows_deleted,
                    "rows_written": result.rows_written,
                    "skipped_outside_session": result.report.get(
                        "skipped_outside_session", 0
                    ),
                }
            )
        context.report_progress(1.0, "готово")
        return {
            "contract_id": contract_id,
            "complete_until": complete_until.isoformat(),
            "builds": results,
        }

    def handler(context: JobContext) -> dict[str, Any]:
        try:
            return run(context)
        except LookupError as error:
            raise JobFailed(str(error)) from error

    return handler


__all__ = ["AGGREGATE_JOB_TYPE", "make_aggregate_handler", "utc_now"]
