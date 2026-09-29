"""Конвейер импорта свечей 1m: validate → coverage → store → report → version.

Импорт **атомарен**: свечи, конфликты, ошибки строк, итог и dataset version
пишутся одной транзакцией. Raw-строки неизменяемы и не удаляются, поэтому
частично записанный сорвавшийся импорт оставил бы «активные» свечи, которых нет
ни в одной версии, и повторный импорт посчитал бы их дубликатами. Запись об
импорте создаётся отдельной транзакцией заранее, чтобы при сбое пометить её
``failed``.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from trader_engine.calendar import TradingCalendar
from trader_engine.coverage import MINUTE, analyze_coverage
from trader_engine.ingest import RawRow, RowError, validate_rows

from trader_db.datasets import extend_dataset_version
from trader_db.derived import enqueue_aggregation
from trader_db.imports import finish_import, insert_candles, start_import
from trader_db.models import DataImport, DataImportError

MAX_STORED_ERRORS = 5000
MAX_REPORTED_INTERVALS = 50
MAX_REPORTED_OUTSIDE = 20

ProgressFn = Callable[[float, str], None]


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    import_id: int
    report: dict[str, Any]
    dataset_version_id: int | None


def _abandon_previous_attempts(session: Session, job_id: int) -> None:
    """Прерванные попытки той же задачи (worker упал) помечаются проваленными."""
    session.execute(
        text(
            "UPDATE data_imports SET status = 'failed', finished_at = now(), "
            "error = 'прерван: задача перезапущена' "
            "WHERE status = 'running' AND params ->> 'job_id' = :job"
        ),
        {"job": str(job_id)},
    )


def _process(
    session: Session,
    import_id: int,
    contract_id: int,
    rows: Iterable[RawRow | RowError],
    calendar: TradingCalendar,
    progress: ProgressFn,
    allow_non_positive_prices: bool,
) -> ImportOutcome:
    progress(0.05, "проверка строк")
    validation = validate_rows(
        rows, allow_non_positive_prices=allow_non_positive_prices
    )
    candles = validation.candles

    stored_errors = validation.errors[:MAX_STORED_ERRORS]
    session.add_all(
        DataImportError(
            data_import_id=import_id,
            row_number=error.row_number,
            raw_row=error.raw,
            reason_code=error.code,
            message=error.message,
        )
        for error in stored_errors
    )

    progress(0.3, "анализ покрытия")
    coverage = analyze_coverage([c.timestamp for c in candles], calendar)

    def on_batch(done: int, total: int) -> None:
        progress(0.4 + 0.5 * done / max(total, 1), f"запись {done} из {total}")

    progress(0.4, "запись в БД")
    inserted = insert_candles(session, import_id, candles, progress=on_batch)

    report: dict[str, Any] = {
        "rows_total": len(candles)
        + validation.exact_duplicates
        + len(validation.errors),
        "rows_valid": len(candles),
        "rows_rejected": len(validation.errors),
        "inserted": inserted.inserted,
        "duplicates": inserted.duplicates + validation.exact_duplicates,
        "duplicates_in_file": validation.exact_duplicates,
        "duplicates_in_db": inserted.duplicates,
        "conflicts": inserted.conflicts,
        "error_counts": validation.error_counts,
        "errors_stored": len(stored_errors),
        "errors_truncated": len(validation.errors) > len(stored_errors),
        "warnings": {
            "out_of_order_rows": validation.out_of_order_rows,
            "outside_session_rows": len(coverage.outside_session),
        },
        "outside_session_sample": [
            moment.isoformat()
            for moment in coverage.outside_session[:MAX_REPORTED_OUTSIDE]
        ],
        "range": (
            {
                "start": candles[0].timestamp.isoformat(),
                "end": candles[-1].timestamp.isoformat(),
            }
            if candles
            else None
        ),
        "min_price": str(min(c.low for c in candles)) if candles else None,
        "max_price": str(max(c.high for c in candles)) if candles else None,
        "missing": {
            "intervals": len(coverage.missing_intervals),
            "minutes": coverage.missing_minutes,
            "sample": [
                {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "minutes": int((end - start) / MINUTE),
                }
                for start, end in coverage.missing_intervals[:MAX_REPORTED_INTERVALS]
            ],
        },
        "dataset_version_id": None,
    }

    progress(0.95, "завершение")
    finish_import(session, import_id, report=report)
    version_id: int | None = None
    if inserted.inserted > 0:
        # Ничего не добавилось (дубликаты/конфликты) — данные не изменились,
        # новая версия не нужна; конфликты создадут её при принятии.
        version_id = extend_dataset_version(session, import_id).id
        data_import = session.get(DataImport, import_id)
        assert data_import is not None
        data_import.report = {**report, "dataset_version_id": version_id}
        report = data_import.report
        # Новые минутные данные — бары надо достроить (ADR-0018); в той же
        # транзакции, чтобы версия и задача появлялись вместе.
        enqueue_aggregation(session, contract_id)
    session.flush()
    return ImportOutcome(import_id, report, version_id)


def run_candle_import(
    session_factory: sessionmaker[Session],
    *,
    provider_code: str,
    contract_id: int,
    source_type: str,
    rows: Iterable[RawRow | RowError],
    calendar: TradingCalendar,
    source_name: str | None = None,
    params: dict[str, Any] | None = None,
    progress: ProgressFn | None = None,
    job_id: int | None = None,
    allow_non_positive_prices: bool = False,
) -> ImportOutcome:
    """Выполнить импорт целиком; при сбое или отмене данные не остаются.

    ``progress(доля, сообщение)`` вызывается по ходу работы; исключение из него
    (например, отмена задачи) откатывает импорт и помечает запись ``failed``.
    """
    report_progress: ProgressFn = progress or (lambda _fraction, _message: None)
    stored_params = dict(params or {})
    if job_id is not None:
        stored_params["job_id"] = job_id

    with session_factory() as session:
        if job_id is not None:
            _abandon_previous_attempts(session, job_id)
        import_id = start_import(
            session,
            provider_code=provider_code,
            contract_id=contract_id,
            source_type=source_type,
            source_name=source_name,
            params=stored_params,
        )
        session.commit()

    try:
        with session_factory() as session:
            outcome = _process(
                session,
                import_id,
                contract_id,
                rows,
                calendar,
                report_progress,
                allow_non_positive_prices,
            )
            session.commit()
    except BaseException as error:
        with session_factory() as session:
            finish_import(
                session,
                import_id,
                status="failed",
                error=f"{type(error).__name__}: {error}"[:2000],
            )
            session.commit()
        raise
    return outcome
