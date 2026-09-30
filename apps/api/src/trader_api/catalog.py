"""REST каталога: root, торговые календари, контракты (ADR-0007, ADR-0014).

Root и контракты заводятся пользователем через UI; список контрактов из ISS
запрашивается задачей ``iss.sync_root`` в режиме ``dry_run`` (ничего не создаёт),
а подтверждённые пользователем серии создаются ``POST /roots/{id}/contracts/from-iss``.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from trader_db import (
    enqueue_statement,
    has_active_contract_job,
    load_trading_calendar,
    upsert_contract,
)
from trader_db.models import (
    CalendarHoliday,
    CalendarRule,
    CalendarSpecialDay,
    Contract,
    ContractProviderId,
    DataProvider,
    Root,
    TradingCalendar,
)

from trader_api.deps import DbSession
from trader_api.jobs import JobOut

router = APIRouter()

ISS_PROVIDER = "moex_iss"
NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Не найдено"}}
CONFLICT: dict[int | str, dict[str, Any]] = {409: {"description": "Конфликт данных"}}


# --- календари ---------------------------------------------------------------


class CalendarOut(BaseModel):
    code: str
    name: str
    timezone: str
    rules: int = Field(description="Сколько эпох расписания (ADR-0014)")
    holidays: int
    special_days: int


class CalendarRuleOut(BaseModel):
    effective_from: date
    effective_to: date | None
    bar_anchor: str
    weekday_windows: list[dict[str, Any]]
    weekend_windows: list[dict[str, Any]]


class CalendarDetail(CalendarOut):
    fingerprint: str = Field(description="Отпечаток расписания: меняется вместе с ним")
    rule_list: list[CalendarRuleOut]


async def _calendar_counts(
    session: AsyncSession, calendar_id: int
) -> tuple[int, int, int]:
    async def count(model: Any) -> int:
        return (
            await session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.calendar_id == calendar_id)
            )
            or 0
        )

    return (
        await count(CalendarRule),
        await count(CalendarHoliday),
        await count(CalendarSpecialDay),
    )


@router.get(
    "/calendars",
    response_model=list[CalendarOut],
    tags=["calendars"],
    operation_id="listCalendars",
    summary="Торговые календари",
    description=(
        "Календари только для чтения: расписание откалибровано по данным ISS и "
        "меняется миграциями (ADR-0014)."
    ),
)
async def list_calendars(session: DbSession) -> list[CalendarOut]:
    result: list[CalendarOut] = []
    for calendar in await session.scalars(
        select(TradingCalendar).order_by(TradingCalendar.code)
    ):
        rules, holidays, special = await _calendar_counts(session, calendar.id)
        result.append(
            CalendarOut(
                code=calendar.code,
                name=calendar.name,
                timezone=calendar.timezone,
                rules=rules,
                holidays=holidays,
                special_days=special,
            )
        )
    return result


@router.get(
    "/calendars/{code}",
    response_model=CalendarDetail,
    tags=["calendars"],
    operation_id="getCalendar",
    summary="Календарь с эпохами расписания",
    responses=NOT_FOUND,
)
async def get_calendar(code: str, session: DbSession) -> CalendarDetail:
    calendar = (
        await session.scalars(
            select(TradingCalendar).where(TradingCalendar.code == code)
        )
    ).one_or_none()
    if calendar is None:
        raise HTTPException(404, "Календарь не найден")
    rules = list(
        await session.scalars(
            select(CalendarRule)
            .where(CalendarRule.calendar_id == calendar.id)
            .order_by(CalendarRule.effective_from)
        )
    )
    _, holidays, special = await _calendar_counts(session, calendar.id)
    fingerprint = (
        await session.run_sync(lambda sync: load_trading_calendar(sync, code))
    ).fingerprint()
    return CalendarDetail(
        code=calendar.code,
        name=calendar.name,
        timezone=calendar.timezone,
        rules=len(rules),
        holidays=holidays,
        special_days=special,
        fingerprint=fingerprint,
        rule_list=[
            CalendarRuleOut(
                effective_from=rule.effective_from,
                effective_to=rule.effective_to,
                bar_anchor=rule.bar_anchor.isoformat(),
                weekday_windows=rule.weekday_windows,
                weekend_windows=rule.weekend_windows,
            )
            for rule in rules
        ],
    )


class CalendarDays(BaseModel):
    holidays: list[date] = Field(description="Будние дни без торгов")
    special_days: list[date] = Field(
        description="Выходные, торгуемые как обычный день (рабочие субботы-переносы)"
    )


@router.get(
    "/calendars/{code}/days",
    response_model=CalendarDays,
    tags=["calendars"],
    operation_id="getCalendarDays",
    summary="Праздники и особые дни календаря",
    responses=NOT_FOUND,
)
async def get_calendar_days(code: str, session: DbSession) -> CalendarDays:
    calendar_id = await session.scalar(
        select(TradingCalendar.id).where(TradingCalendar.code == code)
    )
    if calendar_id is None:
        raise HTTPException(404, "Календарь не найден")
    holidays = await session.scalars(
        select(CalendarHoliday.date)
        .where(CalendarHoliday.calendar_id == calendar_id)
        .order_by(CalendarHoliday.date)
    )
    special = await session.scalars(
        select(CalendarSpecialDay.date)
        .where(CalendarSpecialDay.calendar_id == calendar_id)
        .order_by(CalendarSpecialDay.date)
    )
    return CalendarDays(holidays=list(holidays), special_days=list(special))


# --- root --------------------------------------------------------------------


class RootIn(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "code": "NG",
                    "name": "Природный газ",
                    "quote_currency": "USD",
                    "tick_size": "0.001",
                }
            ]
        }
    )

    code: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[A-Za-z0-9_.-]+$",
        description="Код базового актива (для ISS — ASSETCODE, например NG, BR, GOLD)",
    )
    name: str = Field(min_length=1, max_length=128)
    exchange: str = Field(default="MOEX", min_length=1, max_length=32)
    quote_currency: str = Field(min_length=3, max_length=3, examples=["USD"])
    tick_size: Decimal = Field(gt=0)
    calendar_code: str = "moex_forts"
    roll_trading_days: int = Field(
        default=5, ge=0, le=60, description="Ролл за N торговых дней до экспирации"
    )
    include_weekend_sessions: bool = Field(
        default=False, description="Включать выходные сессии в continuous-серию"
    )


class RootPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    exchange: str | None = Field(default=None, min_length=1, max_length=32)
    quote_currency: str | None = Field(default=None, min_length=3, max_length=3)
    tick_size: Decimal | None = Field(default=None, gt=0)
    calendar_code: str | None = None
    roll_trading_days: int | None = Field(default=None, ge=0, le=60)
    include_weekend_sessions: bool | None = None


class RootOut(BaseModel):
    id: int
    code: str
    name: str
    exchange: str
    quote_currency: str
    tick_size: Decimal
    calendar_code: str
    roll_trading_days: int
    include_weekend_sessions: bool
    created_at: datetime


async def _calendar_id(session: AsyncSession, code: str) -> int:
    calendar_id = await session.scalar(
        select(TradingCalendar.id).where(TradingCalendar.code == code)
    )
    if calendar_id is None:
        raise HTTPException(422, f"Календарь {code!r} не найден")
    return calendar_id


async def _root_out(session: AsyncSession, root: Root) -> RootOut:
    calendar_code = await session.scalar(
        select(TradingCalendar.code).where(TradingCalendar.id == root.calendar_id)
    )
    assert calendar_code is not None
    return RootOut(
        id=root.id,
        code=root.code,
        name=root.name,
        exchange=root.exchange,
        quote_currency=root.quote_currency,
        tick_size=root.tick_size,
        calendar_code=calendar_code,
        roll_trading_days=root.roll_trading_days,
        include_weekend_sessions=root.include_weekend_sessions,
        created_at=root.created_at,
    )


async def _find_root(session: AsyncSession, root_id: int) -> Root:
    root = await session.get(Root, root_id)
    if root is None:
        raise HTTPException(404, "Root не найден")
    return root


@router.get(
    "/roots",
    response_model=list[RootOut],
    tags=["roots"],
    operation_id="listRoots",
    summary="Базовые активы",
)
async def list_roots(session: DbSession) -> list[RootOut]:
    roots = await session.scalars(select(Root).order_by(Root.code))
    return [await _root_out(session, root) for root in roots]


@router.post(
    "/roots",
    response_model=RootOut,
    status_code=201,
    tags=["roots"],
    operation_id="createRoot",
    summary="Завести базовый актив",
    responses={**CONFLICT, 422: {"description": "Неизвестный календарь или значения"}},
)
async def create_root(body: RootIn, session: DbSession) -> RootOut:
    root = Root(
        code=body.code,
        name=body.name,
        exchange=body.exchange,
        quote_currency=body.quote_currency.upper(),
        tick_size=body.tick_size,
        calendar_id=await _calendar_id(session, body.calendar_code),
        roll_trading_days=body.roll_trading_days,
        include_weekend_sessions=body.include_weekend_sessions,
    )
    session.add(root)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(409, f"Root {body.code!r} уже существует") from error
    await session.refresh(root)
    return await _root_out(session, root)


@router.get(
    "/roots/{root_id}",
    response_model=RootOut,
    tags=["roots"],
    operation_id="getRoot",
    summary="Базовый актив",
    responses=NOT_FOUND,
)
async def get_root(root_id: int, session: DbSession) -> RootOut:
    return await _root_out(session, await _find_root(session, root_id))


@router.patch(
    "/roots/{root_id}",
    response_model=RootOut,
    tags=["roots"],
    operation_id="updateRoot",
    summary="Изменить базовый актив",
    description=(
        "Код неизменяем. Смена календаря, числа дней до ролла или флага выходных "
        "сессий приводит к пересборке баров при следующем `aggregate.contract`."
    ),
    responses=NOT_FOUND,
)
async def update_root(root_id: int, body: RootPatch, session: DbSession) -> RootOut:
    root = await _find_root(session, root_id)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if "calendar_code" in changes:
        root.calendar_id = await _calendar_id(session, changes.pop("calendar_code"))
    if "quote_currency" in changes:
        changes["quote_currency"] = changes["quote_currency"].upper()
    for field, value in changes.items():
        setattr(root, field, value)
    await session.commit()
    return await _root_out(session, root)


@router.delete(
    "/roots/{root_id}",
    status_code=204,
    tags=["roots"],
    operation_id="deleteRoot",
    summary="Удалить базовый актив",
    description="Только без контрактов.",
    responses={**NOT_FOUND, **CONFLICT},
)
async def delete_root(root_id: int, session: DbSession) -> None:
    root = await _find_root(session, root_id)
    await session.delete(root)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(409, "У root есть контракты или данные") from error


# --- контракты ---------------------------------------------------------------


class ProviderIdOut(BaseModel):
    provider: str
    id_type: str
    external_id: str


class ContractIn(BaseModel):
    expiration_date: date
    last_trade_date: date | None = None
    secid: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def _last_trade_not_after_expiration(self) -> "ContractIn":
        if self.last_trade_date and self.last_trade_date > self.expiration_date:
            raise ValueError("last_trade_date позже expiration_date")
        return self


class ContractPatch(BaseModel):
    last_trade_date: date | None = None
    secid: str | None = Field(default=None, max_length=32)


class ContractOut(BaseModel):
    id: int
    root_id: int
    expiration_date: date
    last_trade_date: date | None
    secid: str | None
    provider_ids: list[ProviderIdOut]


async def _contract_out(session: AsyncSession, contract: Contract) -> ContractOut:
    rows = await session.execute(
        select(
            DataProvider.code,
            ContractProviderId.id_type,
            ContractProviderId.external_id,
        )
        .join(DataProvider, DataProvider.id == ContractProviderId.provider_id)
        .where(ContractProviderId.contract_id == contract.id)
        .order_by(DataProvider.code, ContractProviderId.id_type)
    )
    return ContractOut(
        id=contract.id,
        root_id=contract.root_id,
        expiration_date=contract.expiration_date,
        last_trade_date=contract.last_trade_date,
        secid=contract.secid,
        provider_ids=[
            ProviderIdOut(provider=provider, id_type=id_type, external_id=external)
            for provider, id_type, external in rows
        ],
    )


async def _find_contract(session: AsyncSession, contract_id: int) -> Contract:
    contract = await session.get(Contract, contract_id)
    if contract is None:
        raise HTTPException(404, "Контракт не найден")
    return contract


@router.get(
    "/roots/{root_id}/contracts",
    response_model=list[ContractOut],
    tags=["contracts"],
    operation_id="listContracts",
    summary="Контракты базового актива",
    description="По возрастанию экспирации.",
    responses=NOT_FOUND,
)
async def list_contracts(root_id: int, session: DbSession) -> list[ContractOut]:
    await _find_root(session, root_id)
    contracts = await session.scalars(
        select(Contract)
        .where(Contract.root_id == root_id)
        .order_by(Contract.expiration_date)
    )
    return [await _contract_out(session, contract) for contract in contracts]


@router.post(
    "/roots/{root_id}/contracts",
    response_model=ContractOut,
    status_code=201,
    tags=["contracts"],
    operation_id="createContract",
    summary="Завести контракт вручную",
    description="Ключ контракта — `(root, expiration_date)`; SECID — лишь атрибут.",
    responses={**NOT_FOUND, **CONFLICT},
)
async def create_contract(
    root_id: int, body: ContractIn, session: DbSession
) -> ContractOut:
    await _find_root(session, root_id)
    contract = Contract(
        root_id=root_id,
        expiration_date=body.expiration_date,
        last_trade_date=body.last_trade_date,
        secid=body.secid,
    )
    session.add(contract)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(409, "Контракт с такой экспирацией уже есть") from error
    return await _contract_out(session, contract)


@router.get(
    "/contracts/{contract_id}",
    response_model=ContractOut,
    tags=["contracts"],
    operation_id="getContract",
    summary="Контракт",
    responses=NOT_FOUND,
)
async def get_contract(contract_id: int, session: DbSession) -> ContractOut:
    return await _contract_out(session, await _find_contract(session, contract_id))


@router.patch(
    "/contracts/{contract_id}",
    response_model=ContractOut,
    tags=["contracts"],
    operation_id="updateContract",
    summary="Изменить контракт",
    description="Экспирация — часть ключа и не меняется.",
    responses={**NOT_FOUND, 422: {"description": "last_trade_date позже экспирации"}},
)
async def update_contract(
    contract_id: int, body: ContractPatch, session: DbSession
) -> ContractOut:
    contract = await _find_contract(session, contract_id)
    changes = body.model_dump(exclude_unset=True)
    last_trade = changes.get("last_trade_date", contract.last_trade_date)
    if last_trade and last_trade > contract.expiration_date:
        raise HTTPException(422, "last_trade_date позже expiration_date")
    for field, value in changes.items():
        setattr(contract, field, value)
    await session.commit()
    return await _contract_out(session, contract)


@router.delete(
    "/contracts/{contract_id}",
    status_code=204,
    tags=["contracts"],
    operation_id="deleteContract",
    summary="Удалить контракт",
    description="Только без загруженных данных и импортов.",
    responses={**NOT_FOUND, **CONFLICT},
)
async def delete_contract(contract_id: int, session: DbSession) -> None:
    contract = await _find_contract(session, contract_id)
    await session.delete(contract)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(409, "У контракта есть данные или импорты") from error


# --- контракты из ISS --------------------------------------------------------


class IssPreviewIn(BaseModel):
    from_year: int = Field(default=2020, ge=1990, le=2100)
    to_year: int | None = Field(default=None, ge=1990, le=2100)
    secid_prefix: str | None = Field(
        default=None, description="Префикс SECID; по умолчанию определяется по ISS"
    )


class IssContractIn(BaseModel):
    """Серия ISS, подтверждённая пользователем."""

    secid: str = Field(min_length=1, max_length=32)
    expiration_date: date
    last_trade_date: date | None = None


class IssContractsIn(BaseModel):
    contracts: list[IssContractIn] = Field(min_length=1, max_length=200)
    enqueue_imports: bool = Field(
        default=True, description="Сразу поставить загрузку истории каждого контракта"
    )


class IssContractsOut(BaseModel):
    contracts: list[ContractOut]
    created: int
    imports_enqueued: list[int] = Field(
        description="id поставленных задач `import.iss`"
    )


@router.post(
    "/roots/{root_id}/iss-preview",
    response_model=JobOut,
    status_code=201,
    tags=["contracts"],
    operation_id="previewIssContracts",
    summary="Запросить список контрактов из ISS",
    description=(
        "Ставит задачу `iss.sync_root` в режиме `dry_run`: ничего не создаётся. "
        "Результат задачи (`result.contracts`) показывается пользователю для "
        "подтверждения, затем вызывается `from-iss`."
    ),
    responses=NOT_FOUND,
)
async def preview_iss_contracts(
    root_id: int, body: IssPreviewIn, session: DbSession
) -> Any:
    await _find_root(session, root_id)
    params: dict[str, Any] = {
        "root_id": root_id,
        "dry_run": True,
        "from_year": body.from_year,
    }
    if body.to_year is not None:
        params["to_year"] = body.to_year
    if body.secid_prefix:
        params["secid_prefix"] = body.secid_prefix
    job = (await session.scalars(enqueue_statement("iss.sync_root", params))).one()
    await session.commit()
    return job


@router.post(
    "/roots/{root_id}/contracts/from-iss",
    response_model=IssContractsOut,
    status_code=201,
    tags=["contracts"],
    operation_id="createContractsFromIss",
    summary="Создать подтверждённые контракты ISS",
    description=(
        "Идемпотентно: существующие контракты обновляются. Для каждого при "
        "`enqueue_imports` ставится загрузка истории (если она уже не ждёт)."
    ),
    responses=NOT_FOUND,
)
async def create_contracts_from_iss(
    root_id: int, body: IssContractsIn, session: DbSession
) -> IssContractsOut:
    await _find_root(session, root_id)
    contracts: list[Contract] = []
    created = 0
    enqueued: list[int] = []
    for item in body.contracts:
        if item.last_trade_date and item.last_trade_date > item.expiration_date:
            raise HTTPException(
                422, f"{item.secid}: last_trade_date позже expiration_date"
            )

        def upsert(sync: Any, item: IssContractIn = item) -> tuple[Contract, bool]:
            return upsert_contract(
                sync,
                root_id=root_id,
                expiration_date=item.expiration_date,
                last_trade_date=item.last_trade_date,
                secid=item.secid,
                provider_code=ISS_PROVIDER,
                external_id=item.secid,
            )

        contract, is_new = await session.run_sync(upsert)
        created += is_new
        contracts.append(contract)
        if body.enqueue_imports and not await session.run_sync(
            lambda sync, cid=contract.id: has_active_contract_job(
                sync, "import.iss", cid
            )
        ):
            job = (
                await session.scalars(
                    enqueue_statement("import.iss", {"contract_id": contract.id})
                )
            ).one()
            enqueued.append(job.id)
    await session.commit()
    return IssContractsOut(
        contracts=[await _contract_out(session, c) for c in contracts],
        created=created,
        imports_enqueued=enqueued,
    )
