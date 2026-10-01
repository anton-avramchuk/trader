"""Задача ``engine.run``: прогон движка событий по свечам инструмента (ADR-0021).

Параметры: ``engine`` (имя из реестра), ``engine_params``; ``instrument_id``;
``timeframe``; необязательный ``mode`` — ``auto`` (продолжить прошлый прогон, если
бары не менялись, иначе начать новый) или ``full`` (всегда новый прогон). Результат —
идентификатор прогона, что сделано и сколько событий записано.
"""

from typing import Any

from sqlalchemy.orm import Session, sessionmaker
from trader_db import advance_run, get_instrument, read_bars
from trader_engine.events import create
from trader_engine.indicators import BarInput
from trader_engine.timeframes import TIMEFRAMES

from trader_worker.handlers import Handler, JobContext, JobFailed

ENGINE_RUN_JOB_TYPE = "engine.run"


def make_engine_run_handler(session_factory: sessionmaker[Session]) -> Handler:
    def handler(context: JobContext) -> dict[str, Any]:
        params = context.params
        instrument_id = params.get("instrument_id")
        if not isinstance(instrument_id, int):
            raise JobFailed("Укажите instrument_id")
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

        context.report_progress(0.0, "чтение свечей")
        with session_factory() as session, session.begin():
            if get_instrument(session, instrument_id) is None:
                raise JobFailed(f"Инструмент {instrument_id} не найден")
            bars = [
                BarInput.from_bar(bar)
                for bar in read_bars(session, instrument_id, timeframe)
            ]
            if not bars:
                raise JobFailed("Нет свечей для прогона: сначала загрузите историю")

            context.report_progress(0.3, f"прогон {name} по {len(bars)} барам")
            result = advance_run(
                session,
                str(name),
                engine_params,
                bars,
                timeframe,
                instrument_id=instrument_id,
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
