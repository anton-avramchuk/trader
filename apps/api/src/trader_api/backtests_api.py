"""REST бэктестов: запуск, результаты, сделки, окна, журнал, блокировки (ADR-0027).

Запуск — ``POST /backtests``: эксперимент создаётся сразу (все входы хранятся в нём),
в очередь ставится задача ``backtest.run`` (ход — ``/jobs/{id}`` и WebSocket).
Test-период связки (root, TF, семейство) открывается один раз; разблокировка —
только явная (``POST /backtest-locks/unlock``) с причиной, она пишется в журнал.
"""

import hashlib
import json
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from trader_db import (
    create_experiment,
    enqueue_statement,
    list_log,
    list_trades,
    list_windows,
    unlock_test_period,
)
from trader_db.models import (
    BacktestExperiment,
    BacktestLog,
    BacktestTestLock,
    BacktestTrade,
    BacktestWindow,
    Root,
)
from trader_engine.aggregation import TIMEFRAMES
from trader_engine.backtest.strategy import ExitRule, StrategySpec
from trader_engine.backtest.walk_forward import OVERRIDES, WalkForwardConfig

from trader_api.deps import DbSession

router = APIRouter(tags=["backtests"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Не найдено"}}
INVALID: dict[int | str, dict[str, Any]] = {422: {"description": "Неверные параметры"}}
MAX_TRADES_PAGE = 5000
MAX_GRID_VALUES = 12
MAX_GRID_COMBINATIONS = 200
BACKTEST_JOB_TYPE = "backtest.run"


class Orm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ExitRuleIn(BaseModel):
    kind: str = Field(description="Стоп: none/atr/structure; цель: none/atr/pattern")
    value: float = Field(default=0.0, ge=0, description="Для atr — размер в ATR")


class StrategyIn(BaseModel):
    """Шаблон «вход по событию» (ADR-0027)."""

    source: Literal["pattern", "level_touch", "level_break"] = "pattern"
    groups: list[str] = Field(default_factory=list, description="Типы; пусто — все")
    side: Literal["follow", "fade"] = "follow"
    quality_min: float | None = Field(default=None, ge=0, le=100)
    quality_max: float | None = Field(default=None, ge=0, le=100)
    trends: list[str] = Field(default_factory=list)
    volatilities: list[str] = Field(default_factory=list)
    since: datetime | None = None
    until: datetime | None = None
    stop: ExitRuleIn = Field(default_factory=lambda: ExitRuleIn(kind="atr", value=1.5))
    target: ExitRuleIn = Field(
        default_factory=lambda: ExitRuleIn(kind="atr", value=3.0)
    )
    max_bars: int | None = Field(default=None, ge=1)

    def to_spec(self) -> StrategySpec:
        return StrategySpec(
            source=self.source,
            groups=tuple(self.groups),
            side=self.side,
            quality_min=self.quality_min,
            quality_max=self.quality_max,
            trends=tuple(self.trends),
            volatilities=tuple(self.volatilities),
            since=self.since,
            until=self.until,
            stop=ExitRule(self.stop.kind, self.stop.value),
            target=ExitRule(self.target.kind, self.target.value),
            max_bars=self.max_bars,
        )


class CostsIn(BaseModel):
    half_spread_ticks: float = Field(default=0.0, ge=0, description="Тики на сторону")
    slippage_ticks: float = Field(default=0.0, ge=0, description="Тики на сторону")
    commission_per_contract: float = Field(
        default=0.0, ge=0, description="₽ за контракт на сторону"
    )


class WalkForwardIn(BaseModel):
    train_days: int = Field(ge=1, le=5000)
    valid_days: int = Field(ge=1, le=5000)
    step_days: int = Field(ge=1, le=5000)
    grid: dict[str, list[float]] = Field(
        default_factory=dict,
        description=f"Сетка параметров ({', '.join(OVERRIDES)}); пусто — один набор",
    )
    objective: Literal["profit_factor", "net"] = "profit_factor"
    min_trades: int = Field(default=10, ge=1)


class BacktestCreate(BaseModel):
    root_id: int
    timeframe: str
    kind: Literal["single", "walk_forward"] = "single"
    strategy: StrategyIn
    costs: CostsIn = Field(default_factory=CostsIn)
    contracts: int = Field(default=1, ge=1, le=1000)
    period_from: date
    period_to: date
    walk_forward: WalkForwardIn | None = None
    test_from: date | None = Field(
        default=None,
        description="Финальный test: после period_to, открывается один раз",
    )
    test_to: date | None = None

    @model_validator(mode="after")
    def _check(self) -> "BacktestCreate":
        if self.period_from > self.period_to:
            raise ValueError("period_from позже period_to")
        if (self.test_from is None) != (self.test_to is None):
            raise ValueError("Test-период задаётся парой test_from и test_to")
        if self.test_from is not None and self.test_to is not None:
            if self.kind != "walk_forward":
                raise ValueError("Test-период — только для walk_forward")
            if self.test_from > self.test_to:
                raise ValueError("test_from позже test_to")
            if self.test_from <= self.period_to:
                raise ValueError("Test-период должен идти строго после period_to")
        if self.kind == "walk_forward" and self.walk_forward is None:
            raise ValueError("Для walk_forward нужна конфигурация окон")
        return self


class BacktestOut(Orm):
    id: int
    kind: str
    status: str = Field(description="queued, running, succeeded, failed")
    root_id: int
    timeframe_code: str
    family: str
    strategy: dict[str, Any]
    costs: dict[str, Any]
    contracts: int
    period_from: date
    period_to: date
    test_from: date | None
    test_to: date | None
    walk_forward: dict[str, Any] | None
    versions: dict[str, Any]
    params_hash: str
    result: dict[str, Any] | None
    error: str | None
    job_id: int | None
    created_at: datetime
    finished_at: datetime | None


class TradeOut(Orm):
    id: int
    segment: str
    window_id: int | None
    sequence: int
    side: str
    ref: str | None
    signal_price: float | None = Field(
        description="Close сигнального бара в шкале continuous (для уровней)"
    )
    contracts: int
    entry_time: datetime
    exit_time: datetime
    reason: str
    legs: list[dict[str, Any]]
    gross_ticks: float
    cost_ticks: float
    mfe_ticks: float
    mae_ticks: float
    gross_points: float
    net_points: float
    commission_rub: float
    gross_rub: float | None
    net_rub: float | None
    step_price_estimated: bool
    ambiguous_bar: bool
    rolled: bool


class WindowOut(Orm):
    id: int
    sequence: int
    train_from: date
    train_to: date
    valid_from: date
    valid_to: date
    params: dict[str, Any]
    train_metrics: dict[str, Any] | None
    valid_metrics: dict[str, Any] | None


class LogOut(Orm):
    id: int
    created_at: datetime
    event: str
    root_id: int | None
    timeframe_code: str | None
    family: str | None
    experiment_id: int | None
    params_hash: str | None
    period_from: date | None
    period_to: date | None
    touches_test: bool
    note: str | None


class LockOut(Orm):
    id: int
    root_id: int
    timeframe_code: str
    family: str
    test_from: date
    test_to: date
    experiment_id: int | None
    created_at: datetime


class UnlockIn(BaseModel):
    root_id: int
    timeframe: str
    family: str
    note: str = Field(min_length=5, description="Причина — попадает в журнал")


class UnlockOut(BaseModel):
    unlocked: bool


def _validate(body: BacktestCreate) -> tuple[StrategySpec, dict[str, Any]]:
    if body.timeframe not in TIMEFRAMES:
        raise HTTPException(
            422,
            f"Неизвестный таймфрейм {body.timeframe!r}; доступны: "
            + ", ".join(TIMEFRAMES),
        )
    spec = body.strategy.to_spec()
    try:
        spec.validate()
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    config: dict[str, Any] | None = None
    if body.walk_forward is not None:
        grid = body.walk_forward.grid
        if any(len(v) == 0 or len(v) > MAX_GRID_VALUES for v in grid.values()):
            raise HTTPException(
                422, f"Значений на параметр сетки: от 1 до {MAX_GRID_VALUES}"
            )
        combos = 1
        for values in grid.values():
            combos *= len(values)
        if combos > MAX_GRID_COMBINATIONS:
            raise HTTPException(
                422, f"В сетке не больше {MAX_GRID_COMBINATIONS} сочетаний"
            )
        config = body.walk_forward.model_dump()
        try:
            WalkForwardConfig(
                train_days=body.walk_forward.train_days,
                valid_days=body.walk_forward.valid_days,
                step_days=body.walk_forward.step_days,
                grid=grid,
                objective=body.walk_forward.objective,
                min_trades=body.walk_forward.min_trades,
            ).validate()
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
    return spec, config or {}


def _params_hash(body: BacktestCreate, spec: StrategySpec) -> str:
    canonical = json.dumps(
        {
            "root_id": body.root_id,
            "timeframe": body.timeframe,
            "kind": body.kind,
            "strategy": spec.to_dict(),
            "costs": body.costs.model_dump(),
            "contracts": body.contracts,
            "period": [body.period_from.isoformat(), body.period_to.isoformat()],
            "walk_forward": body.walk_forward.model_dump()
            if body.walk_forward
            else None,
            "test": [
                None if body.test_from is None else body.test_from.isoformat(),
                None if body.test_to is None else body.test_to.isoformat(),
            ],
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@router.post(
    "/backtests",
    response_model=BacktestOut,
    status_code=201,
    operation_id="createBacktest",
    summary="Запустить бэктест",
    description=(
        "Создаёт эксперимент и ставит задачу `backtest.run` (`job_id` в ответе). "
        "`single` — один период; `walk_forward` — скользящие окна train→validation, "
        "параметры из сетки выбираются только по train, опциональный финальный test "
        "после `period_to` открывается один раз на связку (root, TF, семейство): "
        "повторный запуск получит `test.status = rejected`. Каждый запуск пишется в "
        "журнал `/backtest-log`."
    ),
    responses=NOT_FOUND | INVALID,
)
async def create_backtest(
    body: BacktestCreate, session: DbSession
) -> BacktestExperiment:
    spec, walk_forward = _validate(body)
    if await session.get(Root, body.root_id) is None:
        raise HTTPException(404, f"Root {body.root_id} не найден")
    params_hash = _params_hash(body, spec)

    def create(sync: Any) -> int:
        experiment = create_experiment(
            sync,
            kind=body.kind,
            root_id=body.root_id,
            timeframe_code=body.timeframe,
            family=body.strategy.source,
            strategy=spec.to_dict(),
            costs=body.costs.model_dump(),
            contracts=body.contracts,
            period_from=body.period_from,
            period_to=body.period_to,
            test_from=body.test_from,
            test_to=body.test_to,
            walk_forward=walk_forward or None,
            params_hash=params_hash,
        )
        return int(experiment.id)

    experiment_id = await session.run_sync(create)
    job = (
        await session.scalars(
            enqueue_statement(BACKTEST_JOB_TYPE, {"experiment_id": experiment_id})
        )
    ).one()
    experiment = await session.get_one(BacktestExperiment, experiment_id)
    experiment.job_id = job.id
    await session.commit()
    return experiment


@router.get(
    "/backtests",
    response_model=list[BacktestOut],
    operation_id="listBacktests",
    summary="Список бэктестов",
    description="Новые первыми; фильтры по root, таймфрейму и статусу.",
)
async def list_backtests(
    session: DbSession,
    root_id: int | None = None,
    timeframe: str | None = None,
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[BacktestExperiment]:
    query = select(BacktestExperiment)
    if root_id is not None:
        query = query.where(BacktestExperiment.root_id == root_id)
    if timeframe is not None:
        query = query.where(BacktestExperiment.timeframe_code == timeframe)
    if status is not None:
        query = query.where(BacktestExperiment.status == status)
    rows = await session.scalars(
        query.order_by(BacktestExperiment.id.desc()).limit(limit)
    )
    return list(rows)


@router.get(
    "/backtest-log",
    response_model=list[LogOut],
    operation_id="listBacktestLog",
    summary="Журнал запусков бэктестов",
    description=(
        "Все запуски (включая неудачные), открытия, отклонения и разблокировки "
        "test: по нему видно, сколько вариантов перебрано. Новые первыми."
    ),
)
async def backtest_log(
    session: DbSession,
    root_id: int | None = None,
    timeframe: str | None = None,
    family: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[BacktestLog]:
    return await session.run_sync(
        lambda sync: list_log(
            sync,
            root_id=root_id,
            timeframe_code=timeframe,
            family=family,
            limit=limit,
        )
    )


@router.get(
    "/backtest-locks",
    response_model=list[LockOut],
    operation_id="listBacktestLocks",
    summary="Заблокированные test-периоды",
    description="Test-периоды связок (root, TF, семейство), уже открытые один раз.",
)
async def backtest_locks(
    session: DbSession, root_id: int | None = None
) -> list[BacktestTestLock]:
    query = select(BacktestTestLock).order_by(BacktestTestLock.id)
    if root_id is not None:
        query = query.where(BacktestTestLock.root_id == root_id)
    return list(await session.scalars(query))


@router.post(
    "/backtest-locks/unlock",
    response_model=UnlockOut,
    operation_id="unlockBacktestTest",
    summary="Разблокировать test-период",
    description=(
        "Явная разблокировка связки (root, TF, семейство) — test можно открыть "
        "ещё раз. Причина обязательна и пишется в журнал; разблокировка видна "
        "рядом с результатами как повторное использование test."
    ),
)
async def unlock(body: UnlockIn, session: DbSession) -> UnlockOut:
    done = await session.run_sync(
        lambda sync: unlock_test_period(
            sync,
            root_id=body.root_id,
            timeframe_code=body.timeframe,
            family=body.family,
            note=body.note,
        )
    )
    await session.commit()
    return UnlockOut(unlocked=done)


@router.get(
    "/backtests/{experiment_id}",
    response_model=BacktestOut,
    operation_id="getBacktest",
    summary="Результат бэктеста",
    description=(
        "Состояние, версии и результат: метрики в тиках, пунктах и ₽, equity, "
        "walk-forward и test."
    ),
    responses=NOT_FOUND,
)
async def get_backtest(experiment_id: int, session: DbSession) -> BacktestExperiment:
    experiment = await session.get(BacktestExperiment, experiment_id)
    if experiment is None:
        raise HTTPException(404, f"Бэктест {experiment_id} не найден")
    return experiment


@router.get(
    "/backtests/{experiment_id}/trades",
    response_model=list[TradeOut],
    operation_id="listBacktestTrades",
    summary="Сделки бэктеста",
    description="В порядке сегмента, окна и номера; фильтры по сегменту и окну.",
    responses=NOT_FOUND,
)
async def backtest_trades(
    experiment_id: int,
    session: DbSession,
    segment: Literal["single", "validation", "test"] | None = None,
    window_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_TRADES_PAGE)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[BacktestTrade]:
    if await session.get(BacktestExperiment, experiment_id) is None:
        raise HTTPException(404, f"Бэктест {experiment_id} не найден")
    trades = await session.run_sync(
        lambda sync: list_trades(
            sync, experiment_id, segment=segment, window_id=window_id
        )
    )
    return trades[offset : offset + limit]


@router.get(
    "/backtests/{experiment_id}/windows",
    response_model=list[WindowOut],
    operation_id="listBacktestWindows",
    summary="Окна walk-forward",
    description="Периоды окон, выбранные параметры и метрики train/validation.",
    responses=NOT_FOUND,
)
async def backtest_windows(
    experiment_id: int, session: DbSession
) -> list[BacktestWindow]:
    if await session.get(BacktestExperiment, experiment_id) is None:
        raise HTTPException(404, f"Бэктест {experiment_id} не найден")
    return await session.run_sync(lambda sync: list_windows(sync, experiment_id))
