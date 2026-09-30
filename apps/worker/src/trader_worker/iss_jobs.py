"""Задачи MOEX ISS: синхронизация контрактов root и возобновляемая загрузка 1m.

- ``iss.sync_root`` находит у ISS контракты базового актива (включая истёкшие),
  заводит их в БД и ставит по задаче ``import.iss`` на каждый.
- ``import.iss`` загружает историю одного контракта. Уже загруженные окна дат
  (завершённые импорты ISS) вычитаются, остаток режется на куски по
  ``chunk_days`` дней, каждый кусок — отдельный атомарный импорт. Сорвавшаяся
  загрузка продолжается с первого незагруженного куска, повторный запуск
  догружает только недостающее. Сегодняшний день не грузится: он неполон.
"""

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import exists, select
from sqlalchemy.orm import Session, sessionmaker
from trader_db import (
    enqueue_job,
    load_contract_calendar,
    loaded_windows,
    provider_contract_id,
    run_candle_import,
    upsert_contract,
)
from trader_db.jobs import ACTIVE_STATUSES
from trader_db.models import Job, Root
from trader_engine.intervals import chunk_window, subtract_windows
from trader_providers import HistoricalDataProvider, IssError

from trader_worker.handlers import Handler, JobContext, JobFailed

PROVIDER_CODE = "moex_iss"
IMPORT_JOB_TYPE = "import.iss"
SYNC_JOB_TYPE = "iss.sync_root"
DEFAULT_FROM_YEAR = 2020
DEFAULT_CHUNK_DAYS = 92
MSK = ZoneInfo("Europe/Moscow")

ProviderFactory = Callable[[], HistoricalDataProvider]


def msk_today() -> date:
    return datetime.now(MSK).date()


def _int_param(params: dict[str, Any], key: str) -> int:
    value = params.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise JobFailed(f"Не задан параметр {key} (целое число)")
    return value


def _optional_date(params: dict[str, Any], key: str) -> date | None:
    value = params.get(key)
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as error:
        raise JobFailed(f"Параметр {key}={value!r}: нужна дата ГГГГ-ММ-ДД") from error


def make_sync_root_handler(
    session_factory: sessionmaker[Session],
    provider_factory: ProviderFactory,
    *,
    today: Callable[[], date] = msk_today,
) -> Handler:
    """Обработчик ``iss.sync_root``.

    Параметры: ``root_id``; ``from_year`` (по умолчанию 2020); ``to_year`` (по
    умолчанию следующий год); ``secid_prefix`` (иначе определяется по действующим
    сериям); ``enqueue_imports`` (по умолчанию ``true``); ``dry_run`` — только
    найти контракты и вернуть их (для подтверждения пользователем), ничего не
    создавая и не ставя.
    """

    def run(context: JobContext) -> dict[str, Any]:
        params = context.params
        root_id = _int_param(params, "root_id")
        with session_factory() as session:
            root = session.get(Root, root_id)
            if root is None:
                raise JobFailed(f"Root {root_id} не найден")
            asset_code = root.code
        from_year = int(params.get("from_year", DEFAULT_FROM_YEAR))
        to_year = int(params.get("to_year", today().year + 1))
        provider = provider_factory()
        try:

            def report(done: int, total: int) -> None:
                context.report_progress(
                    0.8 * done / max(total, 1), f"перебор контрактов {done} из {total}"
                )

            contracts = provider.list_contracts(
                asset_code,
                from_year=from_year,
                to_year=to_year,
                secid_prefix=params.get("secid_prefix"),
                progress=report,
            )
        except IssError as error:
            raise JobFailed(str(error)) from error
        finally:
            provider.close()

        found_list = [
            {
                "secid": c.provider_id,
                "expiration_date": c.expiration_date.isoformat(),
                "last_trade_date": (
                    c.last_trade_date.isoformat() if c.last_trade_date else None
                ),
            }
            for c in contracts
        ]
        if params.get("dry_run"):
            context.report_progress(1.0, "готово")
            return {
                "root": asset_code,
                "dry_run": True,
                "contracts_found": len(contracts),
                "contracts": found_list,
            }

        created = enqueued = 0
        with session_factory() as session:
            for found in contracts:
                contract, is_new = upsert_contract(
                    session,
                    root_id=root_id,
                    expiration_date=found.expiration_date,
                    last_trade_date=found.last_trade_date,
                    secid=found.provider_id,
                    provider_code=PROVIDER_CODE,
                    external_id=found.provider_id,
                )
                created += is_new
                if params.get("enqueue_imports", True) and not _import_pending(
                    session, contract.id
                ):
                    enqueue_job(session, IMPORT_JOB_TYPE, {"contract_id": contract.id})
                    enqueued += 1
            session.commit()
        context.report_progress(1.0, "готово")
        return {
            "root": asset_code,
            "contracts_found": len(contracts),
            "contracts_created": created,
            "imports_enqueued": enqueued,
            "contracts": found_list,
        }

    def handler(context: JobContext) -> dict[str, Any]:
        try:
            return run(context)
        except IssError as error:
            raise JobFailed(str(error)) from error

    return handler


def _import_pending(session: Session, contract_id: int) -> bool:
    """Есть ли уже ожидающая или выполняющаяся загрузка этого контракта."""
    return bool(
        session.scalar(
            select(
                exists().where(
                    Job.type == IMPORT_JOB_TYPE,
                    Job.params["contract_id"].as_integer() == contract_id,
                    Job.status.in_(ACTIVE_STATUSES),
                )
            )
        )
    )


def make_iss_import_handler(
    session_factory: sessionmaker[Session],
    provider_factory: ProviderFactory,
    *,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
    today: Callable[[], date] = msk_today,
) -> Handler:
    """Обработчик ``import.iss``.

    Параметры: ``contract_id``; необязательные ``from`` / ``till`` (даты
    ``ГГГГ-ММ-ДД``, ограничивают загружаемое окно).
    """

    def run(context: JobContext) -> dict[str, Any]:
        params = context.params
        contract_id = _int_param(params, "contract_id")
        with session_factory() as session:
            secid = provider_contract_id(session, contract_id, PROVIDER_CODE)
            if secid is None:
                raise JobFailed(
                    f"У контракта {contract_id} нет кода ISS: "
                    f"сначала выполните {SYNC_JOB_TYPE}"
                )
            calendar = load_contract_calendar(session, contract_id)
            covered = loaded_windows(session, contract_id, PROVIDER_CODE)

        provider = provider_factory()
        try:
            available = provider.candle_range(secid)
            if available is None:
                return {
                    "secid": secid,
                    "chunks": 0,
                    "inserted": 0,
                    "note": "у ISS нет минутных свечей",
                }
            first_day = available[0].astimezone(MSK).date()
            last_day = min(
                available[1].astimezone(MSK).date(), today() - timedelta(days=1)
            )
            start = max(first_day, _optional_date(params, "from") or first_day)
            end = min(last_day, _optional_date(params, "till") or last_day)
            if start > end:
                return {
                    "secid": secid,
                    "chunks": 0,
                    "inserted": 0,
                    "note": "нечего загружать",
                }

            chunks = [
                chunk
                for gap in subtract_windows((start, end), covered)
                for chunk in chunk_window(gap, chunk_days)
            ]
            totals = {
                "inserted": 0,
                "duplicates": 0,
                "conflicts": 0,
                "rows_rejected": 0,
            }
            for index, (first, last) in enumerate(chunks):
                context.check()

                def scaled(
                    fraction: float,
                    message: str,
                    index: int = index,
                    first: date = first,
                    last: date = last,
                ) -> None:
                    context.report_progress(
                        (index + fraction) / len(chunks),
                        f"{secid} {first}..{last}: {message}",
                    )

                outcome = run_candle_import(
                    session_factory,
                    provider_code=PROVIDER_CODE,
                    contract_id=contract_id,
                    source_type="api",
                    source_name=f"{secid} {first}..{last}",
                    params={
                        "secid": secid,
                        "window": {"from": first.isoformat(), "till": last.isoformat()},
                    },
                    rows=provider.get_historical_candles(
                        secid, first, last, on_page=lambda _total: context.check()
                    ),
                    calendar=calendar,
                    progress=scaled,
                    job_id=context.job_id,
                )
                for key in totals:
                    totals[key] += int(outcome.report[key])
        except IssError as error:
            raise JobFailed(str(error)) from error
        finally:
            provider.close()
        return {
            "secid": secid,
            "window": [start.isoformat(), end.isoformat()],
            "chunks": len(chunks),
            **totals,
        }

    def handler(context: JobContext) -> dict[str, Any]:
        try:
            return run(context)
        except LookupError as error:
            raise JobFailed(str(error)) from error

    return handler


__all__ = [
    "DEFAULT_CHUNK_DAYS",
    "IMPORT_JOB_TYPE",
    "SYNC_JOB_TYPE",
    "make_iss_import_handler",
    "make_sync_root_handler",
    "msk_today",
]
