"""Прогон периода и walk-forward (ADR-0027).

``evaluate`` прогоняет стратегию на периоде ``[start, end]``: сигналы берутся только
из вхождений, чей сигнальный бар лежит в периоде, а бары обрезаются по концу
периода — исход сделки не использует данные позже него (открытая в конце периода
позиция закрывается по ``end_of_data``). Walk-forward: окна ``train → validation``
скользят по торговым дням; параметры из сетки выбираются **только по train** окна,
по validation считается результат выбранных параметров. Финальные параметры —
самый частый выбор по окнам (при равенстве — более поздний); test-период не входит
ни в окна, ни в выбор: его запуск и блокировка — на стороне вызывающего.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from itertools import product
from typing import Any, Literal

from trader_engine.backtest.results import (
    Metrics,
    PricedTrade,
    compute_metrics,
    price_trades,
    records,
)
from trader_engine.backtest.simulator import Costs, SimBar, Skipped, simulate
from trader_engine.backtest.strategy import (
    BuiltSignals,
    ExitRule,
    StrategySpec,
    build_signals,
)
from trader_engine.stats.pipeline import Series, SeriesOccurrence

Objective = Literal["profit_factor", "net"]
OVERRIDES = ("stop_atr", "target_atr", "max_bars", "quality_min")
MIN_TRAIN_TRADES = 10


@dataclass(frozen=True)
class BacktestData:
    """Данные прогона: ряд (ATR, режим), бары и вхождения."""

    series: Series
    sim_bars: Sequence[SimBar]
    items: Sequence[SeriesOccurrence]
    tick_size: float
    tick_value: float | None = None


@dataclass(frozen=True)
class Evaluation:
    """Результат прогона периода."""

    start: date
    end: date
    priced: list[PricedTrade]
    built: BuiltSignals
    skipped: Skipped


def trading_days(data: BacktestData) -> list[date]:
    """Торговые дни ряда по возрастанию."""
    return sorted({bar.trading_day for bar in data.series.bars})


def evaluate(
    data: BacktestData,
    spec: StrategySpec,
    start: date,
    end: date,
    *,
    costs: Costs | None = None,
    quantity: int = 1,
) -> Evaluation:
    """Стратегия на периоде ``[start, end]`` без заглядывания за ``end``."""
    bars = data.series.bars
    last = -1
    for index, bar in enumerate(bars):
        if bar.trading_day <= end:
            last = index
    items = [
        item
        for item in data.items
        if (index := item.entry_index) is not None
        and start <= bars[index].trading_day <= end
        and index <= last
    ]
    built = build_signals(spec, items)
    result = simulate(
        data.sim_bars[: last + 1],
        built.signals,
        tick_size=data.tick_size,
        costs=costs,
        quantity=quantity,
    )
    priced = price_trades(
        result.trades, tick_size=data.tick_size, tick_value=data.tick_value
    )
    return Evaluation(start, end, priced, built, result.skipped)


def summarize(
    evaluation: Evaluation,
    days: Sequence[date],
    unit: Literal["ticks", "points", "money"],
) -> Metrics:
    """Метрики прогона в единице ``unit``; ``days`` — торговые дни всего ряда."""
    items, missing = records(evaluation.priced, unit)
    metrics = compute_metrics(
        items,
        [d for d in days if evaluation.start <= d <= evaluation.end],
        unit,
    )
    if missing:
        metrics.warnings.append("tick_value_missing")
    return metrics


def apply_params(spec: StrategySpec, params: Mapping[str, Any]) -> StrategySpec:
    """Спецификация с подставленными параметрами сетки."""
    unknown = set(params) - set(OVERRIDES)
    if unknown:
        raise ValueError(f"Неизвестные параметры сетки: {sorted(unknown)}")
    changes: dict[str, Any] = {}
    if "stop_atr" in params:
        changes["stop"] = ExitRule("atr", float(params["stop_atr"]))
    if "target_atr" in params:
        changes["target"] = ExitRule("atr", float(params["target_atr"]))
    if "max_bars" in params:
        changes["max_bars"] = int(params["max_bars"])
    if "quality_min" in params:
        changes["quality_min"] = float(params["quality_min"])
    return replace(spec, **changes)


def param_grid(grid: Mapping[str, Sequence[Any]]) -> list[dict[str, Any]]:
    """Все сочетания сетки в детерминированном порядке; пустая сетка — один набор."""
    unknown = set(grid) - set(OVERRIDES)
    if unknown:
        raise ValueError(f"Неизвестные параметры сетки: {sorted(unknown)}")
    keys = sorted(grid)
    if not keys:
        return [{}]
    return [
        dict(zip(keys, values, strict=True))
        for values in product(*(grid[k] for k in keys))
    ]


@dataclass(frozen=True)
class WalkForwardConfig:
    train_days: int
    valid_days: int
    step_days: int
    grid: Mapping[str, Sequence[Any]] = field(default_factory=dict[str, Sequence[Any]])
    objective: Objective = "profit_factor"
    min_trades: int = MIN_TRAIN_TRADES

    def validate(self) -> None:
        if min(self.train_days, self.valid_days, self.step_days) < 1:
            raise ValueError("Длины окон и шаг — не меньше 1 торгового дня")
        if self.objective not in ("profit_factor", "net"):
            raise ValueError(f"Неизвестная цель: {self.objective}")
        if self.min_trades < 1:
            raise ValueError("min_trades должно быть не меньше 1")
        param_grid(self.grid)


@dataclass(frozen=True)
class Window:
    train: tuple[date, date]
    valid: tuple[date, date]


def make_windows(
    days: Sequence[date], train: int, valid: int, step: int
) -> list[Window]:
    """Скользящие окна по торговым дням: ``train`` дней, затем ``valid`` дней."""
    windows: list[Window] = []
    start = 0
    while start + train + valid <= len(days):
        windows.append(
            Window(
                (days[start], days[start + train - 1]),
                (days[start + train], days[start + train + valid - 1]),
            )
        )
        start += step
    return windows


def score(metrics: Metrics, objective: Objective, min_trades: int) -> float | None:
    """Оценка набора параметров по train; ``None`` — не подходит (мало сделок)."""
    if metrics.trades < min_trades:
        return None
    if objective == "net":
        return metrics.net
    if metrics.profit_factor is not None:
        return metrics.profit_factor
    return float("inf") if metrics.net > 0 else None


@dataclass(frozen=True)
class Candidate:
    params: dict[str, Any]
    trades: int
    net: float
    score: float | None


@dataclass(frozen=True)
class WindowResult:
    window: Window
    params: dict[str, Any] | None
    candidates: list[Candidate]
    train_metrics: Metrics | None
    valid_metrics: Metrics | None
    validation: Evaluation | None


@dataclass(frozen=True)
class WalkForwardResult:
    windows: list[WindowResult]
    final_params: dict[str, Any] | None
    # Метрики всех validation-сделок подряд (единица — тики).
    validation_metrics: Metrics
    warnings: list[str] = field(default_factory=list[str])


def select_params(
    data: BacktestData,
    spec: StrategySpec,
    train: tuple[date, date],
    config: WalkForwardConfig,
    days: Sequence[date],
    *,
    costs: Costs | None = None,
    quantity: int = 1,
) -> tuple[dict[str, Any] | None, list[Candidate], Metrics | None]:
    """Лучший набор сетки по train (только по данным периода train)."""
    candidates: list[Candidate] = []
    best: tuple[tuple[float, float, int], dict[str, Any], Metrics] | None = None
    for position, params in enumerate(param_grid(config.grid)):
        evaluation = evaluate(
            data,
            apply_params(spec, params),
            train[0],
            train[1],
            costs=costs,
            quantity=quantity,
        )
        metrics = summarize(evaluation, days, "ticks")
        value = score(metrics, config.objective, config.min_trades)
        candidates.append(Candidate(params, metrics.trades, metrics.net, value))
        if value is None:
            continue
        key = (value, metrics.net, -position)
        if best is None or key > best[0]:
            best = (key, params, metrics)
    if best is None:
        return None, candidates, None
    return best[1], candidates, best[2]


def _canonical(params: Mapping[str, Any]) -> str:
    return json.dumps(params, sort_keys=True)


def choose_final(windows: Sequence[WindowResult]) -> dict[str, Any] | None:
    """Самый частый выбор по окнам; при равенстве — более поздний."""
    counts: dict[str, int] = {}
    last_seen: dict[str, int] = {}
    chosen: dict[str, dict[str, Any]] = {}
    for position, window in enumerate(windows):
        if window.params is None:
            continue
        key = _canonical(window.params)
        counts[key] = counts.get(key, 0) + 1
        last_seen[key] = position
        chosen[key] = window.params
    if not counts:
        return None
    best = max(counts, key=lambda k: (counts[k], last_seen[k]))
    return chosen[best]


def walk_forward(
    data: BacktestData,
    spec: StrategySpec,
    config: WalkForwardConfig,
    days: Sequence[date],
    *,
    costs: Costs | None = None,
    quantity: int = 1,
) -> WalkForwardResult:
    """Walk-forward по заданным торговым дням (test-период вызывающий исключает)."""
    config.validate()
    spec.validate()
    all_days = trading_days(data)
    results: list[WindowResult] = []
    for window in make_windows(
        days, config.train_days, config.valid_days, config.step_days
    ):
        params, candidates, train_metrics = select_params(
            data, spec, window.train, config, all_days, costs=costs, quantity=quantity
        )
        if params is None:
            results.append(WindowResult(window, None, candidates, None, None, None))
            continue
        validation = evaluate(
            data,
            apply_params(spec, params),
            window.valid[0],
            window.valid[1],
            costs=costs,
            quantity=quantity,
        )
        results.append(
            WindowResult(
                window,
                params,
                candidates,
                train_metrics,
                summarize(validation, all_days, "ticks"),
                validation,
            )
        )
    items, _ = records(
        [p for r in results if r.validation for p in r.validation.priced], "ticks"
    )
    window_days = [
        d for r in results for d in days if r.window.valid[0] <= d <= r.window.valid[1]
    ]
    warnings: list[str] = []
    if not results:
        warnings.append("no_windows")
    elif all(r.params is None for r in results):
        warnings.append("no_qualifying_params")
    elif any(r.params is None for r in results):
        warnings.append("some_windows_without_params")
    return WalkForwardResult(
        results,
        choose_final(results),
        compute_metrics(items, window_days, "ticks"),
        warnings,
    )
