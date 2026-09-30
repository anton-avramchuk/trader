"""Задача ``verify.indicators``: online replay индикаторов на диапазоне баров (#32).

Параметры: ``root_id`` (continuous) или ``contract_id``; ``timeframe``;
необязательные ``start`` / ``end`` (ISO-время, ``[start, end)``), ``indicators`` —
список ``{"name", "params"}`` (по умолчанию все зарегистрированные с параметрами по
умолчанию) и ``max_positions`` (сколько позиций перепроверять «с нуля», по
умолчанию 300). Результат — отчёт о расхождениях batch и online по каждому
индикатору (ADR-0003: расхождение = утечка будущего).
"""

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker
from trader_db import read_bars, read_continuous
from trader_db.models import Contract, Root
from trader_engine.aggregation import TIMEFRAMES
from trader_engine.indicators import (
    BarInput,
    available,
    create,
    verify_online,
)

from trader_worker.handlers import Handler, JobContext, JobFailed

VERIFY_JOB_TYPE = "verify.indicators"
DEFAULT_MAX_POSITIONS = 300
MAX_BARS = 20000


def _time_param(params: dict[str, Any], key: str) -> datetime | None:
    value = params.get(key)
    if value is None:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise JobFailed(f"Параметр {key}={value!r}: нужно время ISO 8601") from error
    if moment.tzinfo is None:
        raise JobFailed(f"Параметр {key}: время должно быть с часовым поясом")
    return moment


def make_verify_handler(session_factory: sessionmaker[Session]) -> Handler:
    def handler(context: JobContext) -> dict[str, Any]:
        params = context.params
        root_id, contract_id = params.get("root_id"), params.get("contract_id")
        if (root_id is None) == (contract_id is None):
            raise JobFailed("Укажите ровно один из: root_id или contract_id")
        timeframe = params.get("timeframe")
        if timeframe not in TIMEFRAMES:
            known = ", ".join(TIMEFRAMES)
            raise JobFailed(f"Неизвестный таймфрейм {timeframe!r}; доступны: {known}")
        start, end = _time_param(params, "start"), _time_param(params, "end")
        max_positions = int(params.get("max_positions", DEFAULT_MAX_POSITIONS))
        if max_positions < 1:
            raise JobFailed("max_positions должен быть положительным")

        requested: list[dict[str, Any]] = params.get("indicators") or [
            {"name": plugin.name, "params": {}} for plugin in available()
        ]
        try:
            specs = [(item["name"], item.get("params") or {}) for item in requested]
            for name, spec_params in specs:
                create(name, spec_params)  # проверка имён и параметров до расчёта
        except (KeyError, TypeError, ValueError) as error:
            raise JobFailed(str(error.args[0] if error.args else error)) from error

        with session_factory() as session:
            if root_id is not None:
                if session.get(Root, root_id) is None:
                    raise JobFailed(f"Root {root_id} не найден")
                bars = [
                    item.bar
                    for item in read_continuous(
                        session,
                        int(root_id),
                        timeframe,
                        start,
                        end,
                        limit=MAX_BARS,
                        tail=True,
                    )
                ]
            else:
                assert contract_id is not None
                if session.get(Contract, contract_id) is None:
                    raise JobFailed(f"Контракт {contract_id} не найден")
                bars = read_bars(
                    session,
                    int(contract_id),
                    timeframe,
                    start,
                    end,
                    limit=MAX_BARS,
                    tail=True,
                )
        inputs = [BarInput.from_bar(bar) for bar in bars]
        if not inputs:
            raise JobFailed("В выбранном диапазоне нет баров")

        reports: list[dict[str, Any]] = []
        for index, (name, spec_params) in enumerate(specs):
            context.report_progress(index / len(specs), f"проверка {name}")
            report = verify_online(
                lambda name=name, spec_params=spec_params: create(name, spec_params),
                inputs,
                max_positions=max_positions,
            )
            reports.append(
                {
                    "indicator": name,
                    "params": spec_params,
                    "bars": report.bars,
                    "positions_checked": report.positions_checked,
                    "values_checked": report.values_checked,
                    "mismatch_count": report.mismatch_count,
                    "ok": report.ok,
                    "mismatches": [
                        {
                            "index": m.index,
                            "timestamp": m.timestamp.isoformat(),
                            "output": m.output,
                            "batch": m.batch,
                            "online": m.online,
                        }
                        for m in report.mismatches
                    ],
                }
            )
        context.report_progress(1.0, "готово")
        return {
            "timeframe": timeframe,
            "bars": len(inputs),
            "range": [
                inputs[0].timestamp.isoformat(),
                inputs[-1].close_time.isoformat(),
            ],
            "ok": all(r["ok"] for r in reports),
            "reports": reports,
        }

    return handler
