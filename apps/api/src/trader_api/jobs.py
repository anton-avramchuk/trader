"""REST и WebSocket для очереди задач (ADR-0002)."""

import asyncio
from datetime import datetime
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession
from trader_db import (
    cancel_statement,
    enqueue_statement,
    get_job_statement,
    list_jobs_statement,
)
from trader_db.models import Job

from trader_api.deps import DbSession

router = APIRouter(tags=["jobs"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Задача не найдена"}}

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


class JobCreate(BaseModel):
    """Новая фоновая задача."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "type": "candles.load",
                    "params": {
                        "instrument_id": 1,
                        "period_from": "2026-01-01",
                        "period_to": "2026-02-01",
                    },
                },
                {
                    "type": "engine.run",
                    "params": {
                        "engine": "levels",
                        "instrument_id": 1,
                        "timeframe": "1h",
                    },
                },
            ]
        }
    )

    type: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9_.-]+$",
        description=(
            "Тип задачи: `candles.load`, `engine.run`, `backtest.run`, "
            "`verify.indicators`, `demo.sleep`. "
            "Неизвестный тип worker завершит ошибкой."
        ),
    )
    params: dict[str, Any] = Field(
        default_factory=dict, description="Параметры, зависят от типа задачи."
    )
    max_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Сколько раз повторять при сбое worker'а.",
    )


class JobOut(BaseModel):
    """Состояние задачи. Статусы: queued, running, succeeded, failed, cancelled."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    params: dict[str, Any]
    status: str
    progress: float
    progress_message: str | None
    result: dict[str, Any] | None
    error: str | None
    attempts: int
    max_attempts: int
    cancel_requested: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


async def _find(session: AsyncSession, job_id: int) -> Job | None:
    return (await session.scalars(get_job_statement(job_id))).one_or_none()


@router.post(
    "/jobs",
    response_model=JobOut,
    status_code=201,
    operation_id="createJob",
    summary="Поставить задачу в очередь",
    description=(
        "Задачу подхватит worker; ход выполнения — `GET /jobs/{id}` или WebSocket."
    ),
)
async def create_job(body: JobCreate, session: DbSession) -> Job:
    job = (
        await session.scalars(
            enqueue_statement(body.type, body.params, max_attempts=body.max_attempts)
        )
    ).one()
    await session.commit()
    return job


@router.get(
    "/jobs",
    response_model=list[JobOut],
    operation_id="listJobs",
    summary="Список задач",
    description="Новые задачи первыми; фильтры по статусу и типу.",
)
async def list_jobs(
    session: DbSession,
    status: str | None = None,
    type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[Job]:
    query = list_jobs_statement(status=status, job_type=type, limit=limit)
    return list((await session.scalars(query)).all())


@router.get(
    "/jobs/{job_id}",
    response_model=JobOut,
    operation_id="getJob",
    summary="Состояние задачи",
    responses=NOT_FOUND,
)
async def get_job(job_id: int, session: DbSession) -> Job:
    job = await _find(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return job


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=JobOut,
    operation_id="cancelJob",
    summary="Отменить задачу",
    description=(
        "Ожидающая отменяется сразу, выполняющаяся — при ближайшей проверке worker'а."
    ),
    responses={**NOT_FOUND, 409: {"description": "Задача уже завершена"}},
)
async def cancel_job(job_id: int, session: DbSession) -> Job:
    if await _find(session, job_id) is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    job = (await session.scalars(cancel_statement(job_id))).one_or_none()
    if job is None:
        raise HTTPException(status_code=409, detail="Задача уже завершена")
    await session.commit()
    return job


@router.websocket("/ws/jobs/{job_id}", name="watch_job")
async def watch_job(websocket: WebSocket, job_id: int) -> None:
    """Шлёт состояние задачи при каждом изменении и закрывается по завершении.

    WebSocket не входит в OpenAPI; формат сообщения — схема `JobOut` (JSON).
    Код закрытия 4404 — задачи нет.
    """
    await websocket.accept()
    sessions = websocket.app.state.sessions
    interval: float = websocket.app.state.job_poll_interval
    last: tuple[Any, ...] | None = None
    try:
        while True:
            async with sessions() as session:
                job = await _find(session, job_id)
            if job is None:
                await websocket.close(code=4404)
                return
            payload = JobOut.model_validate(job).model_dump(mode="json")
            state = (
                payload["status"],
                payload["progress"],
                payload["progress_message"],
                payload["attempts"],
                payload["cancel_requested"],
            )
            if state != last:
                await websocket.send_json(payload)
                last = state
            if payload["status"] in TERMINAL_STATUSES:
                await websocket.close()
                return
            await asyncio.sleep(interval)
    except WebSocketDisconnect:
        return
