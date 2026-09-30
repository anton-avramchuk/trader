"""Задача ``engine.run``: прогон движка событий по истории ряда (ADR-0021, #33).

Параметры: ``engine`` (имя из реестра), ``engine_params``; ``root_id`` (continuous)
или ``contract_id``; ``timeframe``; необязательный ``mode`` — ``auto`` (продолжить
прошлый прогон, если бары не менялись, иначе начать новый) или ``full`` (всегда
новый прогон). Результат — идентификатор прогона, что сделано и сколько событий
записано.
"""

from typing import Any

from sqlalchemy.orm import Session, sessionmaker
from trader_db import (
    advance_run,
    latest_dataset_version,
    read_bars,
    read_continuous,
)
from trader_db.models import Contract, Root
from trader_engine.aggregation import TIMEFRAMES
from trader_engine.events import create
from trader_engine.indicators import BarInput

from trader_worker.handlers import Handler, JobContext, JobFailed

ENGINE_RUN_JOB_TYPE = "engine.run"


def make_engine_run_handler(session_factory: sessionmaker[Session]) -> Handler:
    def handler(context: JobContext) -> dict[str, Any]:
        params = context.params
        root_id, contract_id = params.get("root_id"), params.get("contract_id")
        if (root_id is None) == (contract_id is None):
            raise JobFailed("Укажите ровно один из: root_id или contract_id")
        timeframe = params.get("timeframe")
        if timeframe not in TIMEFRAMES:
            known = ", ".join(TIMEFRAMES)
            raise JobFailed(f"Неизвестный таймфрейм {timeframe!r}; доступны: {known}")
        mode = params.get("mode", "auto")
        if mode not in ("auto", "full"):
            raise JobFailed(f"Неизвестный режим {mode!r}; доступны: auto, full")
        name = params.get("engine")
        engine_params = params.get("engine_params") or {}
        try:
            create(str(name), engine_params)  # проверка имени и параметров заранее
        except (KeyError, ValueError) as error:
            raise JobFailed(str(error.args[0] if error.args else error)) from error

        context.report_progress(0.0, "чтение баров")
        with session_factory() as session, session.begin():
            dataset_version_id: int | None = None
            if root_id is not None:
                if session.get(Root, root_id) is None:
                    raise JobFailed(f"Root {root_id} не найден")
                bars = [
                    BarInput.from_bar(item.bar)
                    for item in read_continuous(session, int(root_id), timeframe)
                ]
            else:
                assert contract_id is not None
                if session.get(Contract, contract_id) is None:
                    raise JobFailed(f"Контракт {contract_id} не найден")
                bars = [
                    BarInput.from_bar(bar)
                    for bar in read_bars(session, int(contract_id), timeframe)
                ]
                version = latest_dataset_version(session, int(contract_id))
                dataset_version_id = None if version is None else version.id
            if not bars:
                raise JobFailed("Нет баров для прогона")

            context.report_progress(0.3, f"прогон {name} по {len(bars)} барам")
            result = advance_run(
                session,
                str(name),
                engine_params,
                bars,
                timeframe,
                contract_id=None if contract_id is None else int(contract_id),
                root_id=None if root_id is None else int(root_id),
                dataset_version_id=dataset_version_id,
                mode=mode,
            )
        context.report_progress(1.0, "готово")
        return {
            "run_id": result.run_id,
            "outcome": result.outcome,
            "reason": result.reason,
            "bars_total": result.bars_total,
            "bars_new": result.bars_new,
            "events_written": result.events_written,
        }

    return handler
