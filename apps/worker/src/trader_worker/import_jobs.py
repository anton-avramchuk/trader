"""Задачи импорта свечей 1m: общий обработчик поверх конвейера ``run_candle_import``.

Конкретный источник (CSV, ISS…) поставляет только строки: остальное — валидация,
покрытие, атомарная запись, отчёт и dataset version — общее и живёт в
``trader_db.import_pipeline``. Отмена задачи и остановка worker'а откатывают
импорт целиком.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session, sessionmaker
from trader_db import load_contract_calendar, run_candle_import
from trader_engine.ingest import RawRow, RowError

from trader_worker.handlers import Handler, HandlerRegistry, JobContext


@dataclass(frozen=True, slots=True)
class ImportSource:
    """Что источник сообщает конвейеру: откуда данные и сами строки."""

    provider_code: str
    source_type: str
    rows: Iterable[RawRow | RowError]
    source_name: str | None = None


SourceFactory = Callable[[JobContext], ImportSource]


def make_import_handler(
    session_factory: sessionmaker[Session], source_factory: SourceFactory
) -> Handler:
    """Обработчик задачи импорта; в ``params`` обязателен ``contract_id``.

    Необязательный ``allow_non_positive_prices`` разрешает нулевые и отрицательные
    цены. Результат задачи — id импорта, id версии и полный отчёт.
    """

    def handler(context: JobContext) -> dict[str, Any]:
        contract_id = int(context.params["contract_id"])
        with session_factory() as session:
            calendar = load_contract_calendar(session, contract_id)
        source = source_factory(context)
        outcome = run_candle_import(
            session_factory,
            provider_code=source.provider_code,
            contract_id=contract_id,
            source_type=source.source_type,
            source_name=source.source_name,
            params=dict(context.params),
            rows=source.rows,
            calendar=calendar,
            progress=context.report_progress,
            job_id=context.job_id,
            allow_non_positive_prices=bool(
                context.params.get("allow_non_positive_prices", False)
            ),
        )
        return {
            "import_id": outcome.import_id,
            "dataset_version_id": outcome.dataset_version_id,
            "report": outcome.report,
        }

    return handler


def register_import_job(
    registry: HandlerRegistry,
    job_type: str,
    session_factory: sessionmaker[Session],
    source_factory: SourceFactory,
) -> None:
    registry.register(job_type)(make_import_handler(session_factory, source_factory))
