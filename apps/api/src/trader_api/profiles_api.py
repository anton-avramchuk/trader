"""REST профилей графика: индикаторы с source TF, слои и стили (ADR-0010).

Профиль глобальный или привязан к root; последний применённый запоминается
(`POST …/use`), UI открывает график с ним. Конфигурация проверяется при
сохранении: неизвестные индикаторы, неверные параметры и source TF младше
chart TF отклоняются.
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from trader_db.models import ChartProfile, Instrument
from trader_engine.indicators import create, validate_source_timeframe

from trader_api.deps import DbSession

router = APIRouter(tags=["profiles"])

InstrumentFilter = Annotated[int | None, Query(description="Фильтр по инструменту")]

NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Не найдено"}}
CONFLICT: dict[int | str, dict[str, Any]] = {
    409: {"description": "Профиль с таким именем уже есть"}
}


class ProfileIndicator(BaseModel):
    name: str = Field(description="Имя из `GET /indicators`")
    params: dict[str, Any] = Field(default_factory=dict)
    source_timeframe: str = Field(description="TF, по свечам которого считается")


class ProfileConfig(BaseModel):
    chart_timeframe: str | None = Field(
        default=None, description="TF графика при сохранении (необязательно)"
    )
    indicators: list[ProfileIndicator] = Field(default_factory=list)
    layers: dict[str, bool] = Field(
        default_factory=dict, description="Включённые слои (объём, ролл-маркеры, …)"
    )
    style: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_indicators(self) -> "ProfileConfig":
        for item in self.indicators:
            try:
                create(item.name, item.params)
            except KeyError as error:
                raise ValueError(str(error.args[0])) from error
            reference = self.chart_timeframe or item.source_timeframe
            validate_source_timeframe(reference, item.source_timeframe)
        return self


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    instrument_id: int | None = Field(
        default=None,
        description="Пусто — глобальный профиль, иначе для этого инструмента",
    )
    config: ProfileConfig = Field(default_factory=ProfileConfig)


class ProfilePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    config: ProfileConfig | None = None


class ProfileOut(BaseModel):
    id: int
    name: str
    instrument_id: int | None
    config: ProfileConfig
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None


def _out(profile: ChartProfile) -> ProfileOut:
    return ProfileOut(
        id=profile.id,
        name=profile.name,
        instrument_id=profile.instrument_id,
        config=ProfileConfig.model_validate(profile.config),
        created_at=profile.created_at,
        updated_at=profile.updated_at,
        last_used_at=profile.last_used_at,
    )


async def _find(session: AsyncSession, profile_id: int) -> ChartProfile:
    profile = await session.get(ChartProfile, profile_id)
    if profile is None:
        raise HTTPException(404, "Профиль не найден")
    return profile


@router.get(
    "/chart-profiles",
    response_model=list[ProfileOut],
    operation_id="listChartProfiles",
    summary="Профили графика",
    description=(
        "С `instrument_id` — глобальные и профили этого инструмента; без него — все. "
        "Последние применённые первыми."
    ),
)
async def list_profiles(
    session: DbSession, instrument_id: InstrumentFilter = None
) -> list[ProfileOut]:
    query = select(ChartProfile)
    if instrument_id is not None:
        query = query.where(
            or_(
                ChartProfile.instrument_id.is_(None),
                ChartProfile.instrument_id == instrument_id,
            )
        )
    query = query.order_by(
        ChartProfile.last_used_at.desc().nulls_last(),
        ChartProfile.name,
    )
    return [_out(p) for p in await session.scalars(query)]


@router.post(
    "/chart-profiles",
    response_model=ProfileOut,
    status_code=201,
    operation_id="createChartProfile",
    summary="Сохранить профиль",
    responses={**NOT_FOUND, **CONFLICT},
)
async def create_profile(body: ProfileIn, session: DbSession) -> ProfileOut:
    if (
        body.instrument_id is not None
        and await session.get(Instrument, body.instrument_id) is None
    ):
        raise HTTPException(404, "Инструмент не найден")
    profile = ChartProfile(
        name=body.name.strip(),
        instrument_id=body.instrument_id,
        config=body.config.model_dump(mode="json"),
    )
    session.add(profile)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(409, f"Профиль {body.name!r} уже есть") from error
    await session.refresh(profile)
    return _out(profile)


@router.get(
    "/chart-profiles/{profile_id}",
    response_model=ProfileOut,
    operation_id="getChartProfile",
    summary="Профиль",
    responses=NOT_FOUND,
)
async def get_profile(profile_id: int, session: DbSession) -> ProfileOut:
    return _out(await _find(session, profile_id))


@router.patch(
    "/chart-profiles/{profile_id}",
    response_model=ProfileOut,
    operation_id="updateChartProfile",
    summary="Переименовать или обновить конфигурацию",
    responses={**NOT_FOUND, **CONFLICT},
)
async def update_profile(
    profile_id: int, body: ProfilePatch, session: DbSession
) -> ProfileOut:
    profile = await _find(session, profile_id)
    if body.name is not None:
        profile.name = body.name.strip()
    if body.config is not None:
        profile.config = body.config.model_dump(mode="json")
    profile.updated_at = func.now()
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(409, f"Профиль {body.name!r} уже есть") from error
    await session.refresh(profile)
    return _out(profile)


@router.post(
    "/chart-profiles/{profile_id}/use",
    response_model=ProfileOut,
    operation_id="useChartProfile",
    summary="Запомнить как последний применённый",
    responses=NOT_FOUND,
)
async def use_profile(profile_id: int, session: DbSession) -> ProfileOut:
    profile = await _find(session, profile_id)
    profile.last_used_at = func.now()
    await session.commit()
    await session.refresh(profile)
    return _out(profile)


@router.delete(
    "/chart-profiles/{profile_id}",
    status_code=204,
    operation_id="deleteChartProfile",
    summary="Удалить профиль",
    responses=NOT_FOUND,
)
async def delete_profile(profile_id: int, session: DbSession) -> None:
    await session.delete(await _find(session, profile_id))
    await session.commit()
