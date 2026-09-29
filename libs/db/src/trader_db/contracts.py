"""Контракты и покрытие загруженной истории поставщика."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session
from trader_engine.intervals import DateWindow, merge_windows

from trader_db.models import (
    Contract,
    ContractProviderId,
    DataImport,
    DataProvider,
)


def _provider_id(session: Session, provider_code: str) -> int:
    provider_id = session.scalars(
        select(DataProvider.id).where(DataProvider.code == provider_code)
    ).one_or_none()
    if provider_id is None:
        raise LookupError(f"Провайдер {provider_code!r} не найден")
    return provider_id


def upsert_contract(
    session: Session,
    *,
    root_id: int,
    expiration_date: date,
    last_trade_date: date | None,
    secid: str,
    provider_code: str,
    external_id: str,
    id_type: str = "secid",
) -> tuple[Contract, bool]:
    """Создать контракт или обновить его изменяемые атрибуты.

    Ключ — ``(root_id, expiration_date)``; SECID — лишь атрибут. Код у поставщика
    записывается в ``contract_provider_ids``. Возвращает контракт и признак
    «создан сейчас».
    """
    contract = session.scalars(
        select(Contract).where(
            Contract.root_id == root_id, Contract.expiration_date == expiration_date
        )
    ).one_or_none()
    created = contract is None
    if contract is None:
        contract = Contract(
            root_id=root_id,
            expiration_date=expiration_date,
            last_trade_date=last_trade_date,
            secid=secid,
        )
        session.add(contract)
    else:
        contract.last_trade_date = last_trade_date
        contract.secid = secid
    session.flush()

    provider_id = _provider_id(session, provider_code)
    link = session.get(ContractProviderId, (contract.id, provider_id, id_type))
    if link is None:
        session.add(
            ContractProviderId(
                contract_id=contract.id,
                provider_id=provider_id,
                id_type=id_type,
                external_id=external_id,
            )
        )
    else:
        link.external_id = external_id
    session.flush()
    return contract, created


def provider_contract_id(
    session: Session, contract_id: int, provider_code: str, id_type: str = "secid"
) -> str | None:
    """Код контракта у поставщика; ``None``, если не задан."""
    return session.scalars(
        select(ContractProviderId.external_id)
        .join(DataProvider, DataProvider.id == ContractProviderId.provider_id)
        .where(
            ContractProviderId.contract_id == contract_id,
            DataProvider.code == provider_code,
            ContractProviderId.id_type == id_type,
        )
    ).one_or_none()


def loaded_windows(
    session: Session, contract_id: int, provider_code: str
) -> list[DateWindow]:
    """Окна дат, уже загруженные у поставщика (по завершённым импортам, слитые).

    Импорт записывает запрошенное окно в ``params.window`` (``from`` и ``till``,
    включительно). Упавшие и выполняющиеся импорты покрытием не считаются.
    """
    rows = session.execute(
        select(
            DataImport.params["window"]["from"].as_string(),
            DataImport.params["window"]["till"].as_string(),
        )
        .join(DataProvider, DataProvider.id == DataImport.provider_id)
        .where(
            DataProvider.code == provider_code,
            DataImport.contract_id == contract_id,
            DataImport.kind == "import",
            DataImport.status == "completed",
            DataImport.params.has_key("window"),
        )
    ).all()
    return merge_windows(
        (date.fromisoformat(first), date.fromisoformat(last)) for first, last in rows
    )
