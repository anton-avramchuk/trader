"""REST и WebSocket для очереди задач (ADR-0002)."""

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
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

router = APIRouter(tags=["jobs"])

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


class JobCreate(BaseModel):
    type: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_.-]+$")
    params: dict[str, Any] = Field(default_factory=dict)
    max_attempts: int = Field(default=3, ge=1, le=10)


class JobOut(BaseModel):
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


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessions() as session:
        yield session


Session = Annotated[AsyncSession, Depends(get_session)]


async def _find(session: AsyncSession, job_id: int) -> Job | None:
    return (await session.scalars(get_job_statement(job_id))).one_or_none()


@router.post("/jobs", response_model=JobOut, status_code=201)
async def create_job(body: JobCreate, session: Session) -> Job:
    job = (
        await session.scalars(
            enqueue_statement(body.type, body.params, max_attempts=body.max_attempts)
        )
    ).one()
    await session.commit()
    return job


@router.get("/jobs", response_model=list[JobOut])
async def list_jobs(
    session: Session,
    status: str | None = None,
    type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[Job]:
    query = list_jobs_statement(status=status, job_type=type, limit=limit)
    return list((await session.scalars(query)).all())


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: int, session: Session) -> Job:
    job = await _find(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return job


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: int, session: Session) -> Job:
    if await _find(session, job_id) is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    job = (await session.scalars(cancel_statement(job_id))).one_or_none()
    if job is None:
        raise HTTPException(status_code=409, detail="Задача уже завершена")
    await session.commit()
    return job


@router.websocket("/ws/jobs/{job_id}")
async def watch_job(websocket: WebSocket, job_id: int) -> None:
    """Шлёт состояние задачи при каждом изменении и закрывается по завершении.

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
