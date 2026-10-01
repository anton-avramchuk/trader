"""REST ручных сеток Фибоначчи (две точки на графике, ADR-0012).

Ручные сетки хранятся отдельно от автоматических (события движка `fibonacci`) и
в статистике не участвуют. Уровни считает та же функция, что у автосетки.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import AwareDatetime, BaseModel, Field, model_validator
from sqlalchemy import select
from trader_db.models import Instrument, ManualFibGrid
from trader_engine.events.fibonacci import fib_levels
from trader_engine.timeframes import TIMEFRAMES

from trader_api.deps import DbSession

router = APIRouter(tags=["engines"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Не найдено"}}


class FibPoint(BaseModel):
    time: AwareDatetime
    price: float


class FibGridIn(BaseModel):
    instrument_id: int
    timeframe: str
    start: FibPoint
    end: FibPoint
    label: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _check(self) -> "FibGridIn":
        if self.timeframe not in TIMEFRAMES:
            raise ValueError(f"Неизвестный таймфрейм {self.timeframe!r}")
        if self.start.time == self.end.time:
            raise ValueError("Точки должны различаться по времени")
        if self.start.price == self.end.price:
            raise ValueError("Точки должны различаться по цене")
        return self


class FibGridOut(BaseModel):
    id: int
    instrument_id: int
    timeframe: str
    start: FibPoint
    end: FibPoint
    label: str | None
    direction: str
    retracement: dict[str, float]
    extension: dict[str, float]
    manual: bool = Field(default=True, description="Ручная сетка (не в статистике)")
    created_at: datetime


def _out(grid: ManualFibGrid) -> FibGridOut:
    start, end = float(grid.start_price), float(grid.end_price)
    levels = fib_levels(start, end)
    return FibGridOut(
        id=grid.id,
        instrument_id=grid.instrument_id,
        timeframe=grid.timeframe_code,
        start=FibPoint(time=grid.start_time, price=start),
        end=FibPoint(time=grid.end_time, price=end),
        label=grid.label,
        direction="up" if end > start else "down",
        retracement=levels["retracement"],
        extension=levels["extension"],
        created_at=grid.created_at,
    )


@router.get(
    "/fib-grids",
    response_model=list[FibGridOut],
    operation_id="listFibGrids",
    summary="Ручные сетки Фибоначчи",
)
async def list_grids(
    session: DbSession,
    instrument_id: Annotated[int | None, Query()] = None,
    timeframe: Annotated[str | None, Query()] = None,
) -> list[FibGridOut]:
    query = select(ManualFibGrid)
    if instrument_id is not None:
        query = query.where(ManualFibGrid.instrument_id == instrument_id)
    if timeframe is not None:
        query = query.where(ManualFibGrid.timeframe_code == timeframe)
    grids = await session.scalars(query.order_by(ManualFibGrid.id))
    return [_out(grid) for grid in grids]


@router.post(
    "/fib-grids",
    response_model=FibGridOut,
    status_code=201,
    operation_id="createFibGrid",
    summary="Сохранить ручную сетку",
    responses=NOT_FOUND,
)
async def create_grid(body: FibGridIn, session: DbSession) -> FibGridOut:
    if await session.get(Instrument, body.instrument_id) is None:
        raise HTTPException(404, f"Инструмент {body.instrument_id} не найден")
    grid = ManualFibGrid(
        instrument_id=body.instrument_id,
        timeframe_code=body.timeframe,
        start_time=body.start.time,
        start_price=Decimal(str(body.start.price)),
        end_time=body.end.time,
        end_price=Decimal(str(body.end.price)),
        label=body.label,
    )
    session.add(grid)
    await session.commit()
    await session.refresh(grid)
    return _out(grid)


@router.delete(
    "/fib-grids/{grid_id}",
    status_code=204,
    operation_id="deleteFibGrid",
    summary="Удалить ручную сетку",
    responses=NOT_FOUND,
)
async def delete_grid(grid_id: int, session: DbSession) -> Response:
    grid = await session.get(ManualFibGrid, grid_id)
    if grid is None:
        raise HTTPException(404, f"Сетка {grid_id} не найдена")
    await session.delete(grid)
    await session.commit()
    return Response(status_code=204)
