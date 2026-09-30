"""Задачи стоимости шага цены: загрузка из дневной истории ISS (ADR-0007).

- ``iss.step_prices`` (``contract_id``, необязательные ``from`` / ``till``) —
  выводит стоимость шага по дням из оборота, объёма и средневзвешенной цены и
  записывает в ``contract_step_prices``. По умолчанию догружает с последнего
  сохранённого дня (его перезаписывает) до вчера.
- ``iss.step_prices_all`` — ставит ``iss.step_prices`` по всем контрактам с кодом
  ISS, которые ещё торгуются или закончились недавно (для расписания).
"""

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from trader_db import (
    enqueue_job,
    has_active_contract_job,
    last_step_price_date,
    provider_contract_id,
    upsert_step_prices,
)
from trader_db.models import Contract, ContractProviderId, DataProvider, Root
from trader_engine.step_price import derive_step_price
from trader_providers import IssError

from trader_worker.handlers import Handler, JobContext, JobFailed
from trader_worker.iss_jobs import MSK, PROVIDER_CODE, ProviderFactory, msk_today

STEP_PRICES_JOB_TYPE = "iss.step_prices"
STEP_PRICES_ALL_JOB_TYPE = "iss.step_prices_all"
# Контракт после экспирации обновляем ещё столько дней (поправки ISS задним числом).
RECENTLY_EXPIRED_DAYS = 7


def _date_param(params: dict[str, Any], key: str) -> date | None:
    value = params.get(key)
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as error:
        raise JobFailed(f"Параметр {key}={value!r}: нужна дата ГГГГ-ММ-ДД") from error


def make_step_prices_handler(
    session_factory: sessionmaker[Session],
    provider_factory: ProviderFactory,
    *,
    today: Callable[[], date] = msk_today,
) -> Handler:
    def handler(context: JobContext) -> dict[str, Any]:
        params = context.params
        contract_id = params.get("contract_id")
        if isinstance(contract_id, bool) or not isinstance(contract_id, int):
            raise JobFailed("Не задан параметр contract_id (целое число)")
        with session_factory() as session:
            contract = session.get(Contract, contract_id)
            if contract is None:
                raise JobFailed(f"Контракт {contract_id} не найден")
            root = session.get(Root, contract.root_id)
            assert root is not None
            tick_size = root.tick_size
            secid = provider_contract_id(session, contract_id, PROVIDER_CODE)
            if secid is None:
                raise JobFailed(
                    f"У контракта {contract_id} нет кода ISS: сначала iss.sync_root"
                )
            last_saved = last_step_price_date(session, contract_id)

        yesterday = today() - timedelta(days=1)
        end = min(_date_param(params, "till") or yesterday, yesterday)
        provider = provider_factory()
        try:
            start = _date_param(params, "from") or last_saved
            if start is None:
                available = provider.candle_range(secid)
                if available is None:
                    return {"secid": secid, "days": 0, "note": "у ISS нет данных"}
                start = available[0].astimezone(MSK).date()
            if start > end:
                return {"secid": secid, "days": 0, "note": "нечего загружать"}
            context.report_progress(0.1, f"{secid}: дневная история {start}..{end}")
            history = provider.daily_history(secid, start, end)
        except IssError as error:
            raise JobFailed(str(error)) from error
        finally:
            provider.close()

        prices: list[tuple[date, Decimal]] = []
        skipped = 0
        for day in history:
            price = derive_step_price(day.value, day.volume, day.waprice, tick_size)
            if price is None:
                skipped += 1
            else:
                prices.append((day.trade_date, price))
        with session_factory() as session:
            saved = upsert_step_prices(session, contract_id, prices)
            session.commit()
        context.report_progress(1.0, "готово")
        return {
            "secid": secid,
            "window": [start.isoformat(), end.isoformat()],
            "days": saved,
            "skipped_days": skipped,
        }

    return handler


def make_step_prices_all_handler(
    session_factory: sessionmaker[Session],
    *,
    today: Callable[[], date] = msk_today,
) -> Handler:
    def handler(context: JobContext) -> dict[str, Any]:
        threshold = today() - timedelta(days=RECENTLY_EXPIRED_DAYS)
        enqueued = 0
        with session_factory() as session:
            contracts = session.scalars(
                select(Contract)
                .join(ContractProviderId, ContractProviderId.contract_id == Contract.id)
                .join(DataProvider, DataProvider.id == ContractProviderId.provider_id)
                .where(
                    DataProvider.code == PROVIDER_CODE,
                    Contract.expiration_date >= threshold,
                )
                .order_by(Contract.expiration_date)
            ).all()
            for contract in contracts:
                if not has_active_contract_job(
                    session, STEP_PRICES_JOB_TYPE, contract.id
                ):
                    enqueue_job(
                        session, STEP_PRICES_JOB_TYPE, {"contract_id": contract.id}
                    )
                    enqueued += 1
            session.commit()
        context.report_progress(1.0, "готово")
        return {"contracts": len(contracts), "enqueued": enqueued}

    return handler
