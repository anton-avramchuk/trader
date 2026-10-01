"""Задача ``backtest.run``: бэктест по continuous-серии root (ADR-0027, #158).

Параметр один — ``experiment_id`` созданного API эксперимента: все входы (стратегия,
издержки, период, walk-forward, test) лежат в его строке. Задача читает continuous-
бары и роллы, прогоняет нужные движки событий (как ``engine.run``), собирает
``BacktestData`` и выполняет прогон: одиночный период либо walk-forward с финальным
test-периодом. Test открывается один раз на связку (root, TF, семейство): повтор
отклоняется и пишется в журнал. Сделки, окна и метрики сохраняются в БД.
"""

from bisect import bisect_right
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from math import isinf
from typing import Any

from sqlalchemy.orm import Session, sessionmaker
from trader_db import (
    add_trades,
    add_window,
    advance_run,
    count_runs,
    fail_experiment,
    finish_experiment,
    list_step_prices,
    load_events,
    mark_running,
    open_test_period,
    read_continuous,
)
from trader_db.continuous import load_rolls
from trader_db.models import BacktestExperiment, EngineRun, Root
from trader_engine.aggregation import TIMEFRAMES
from trader_engine.backtest.results import (
    PricedTrade,
    equity_curve,
    records,
)
from trader_engine.backtest.simulator import Costs, SimBar
from trader_engine.backtest.strategy import StrategySpec
from trader_engine.backtest.walk_forward import (
    BacktestData,
    Evaluation,
    WalkForwardConfig,
    WalkForwardResult,
    apply_params,
    evaluate,
    summarize,
    trading_days,
    walk_forward,
)
from trader_engine.indicators import BarInput
from trader_engine.stats.pipeline import build_series, collect

from trader_worker.handlers import (
    Handler,
    JobCancelled,
    JobContext,
    JobFailed,
    JobInterrupted,
    JobLost,
)

BACKTEST_JOB_TYPE = "backtest.run"
PATTERN_ENGINES = ("double_triple", "head_shoulders", "trendlines", "range_breakout")
TEMPLATE_VERSION = 1
UNITS: tuple[str, ...] = ("ticks", "points", "rub")
INF_REPLACEMENT = 1e9  # JSON не хранит inf


def _safe(value: Any) -> Any:
    """Значение для JSONB: даты — строки, dataclass — словарь, inf — число."""
    if is_dataclass(value) and not isinstance(value, type):
        return _safe(asdict(value))
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}  # type: ignore[reportUnknownVariableType]
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]  # type: ignore[reportUnknownVariableType]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and isinf(value):
        return INF_REPLACEMENT if value > 0 else -INF_REPLACEMENT
    return value


def _trade_row(priced: PricedTrade) -> dict[str, Any]:
    trade = priced.trade
    return {
        "side": trade.side,
        "ref": trade.ref,
        "contracts": trade.contracts,
        "entry_time": trade.legs[0].entry_time,
        "exit_time": priced.exit_time,
        "reason": trade.reason,
        "legs": _safe(list(trade.legs)),
        "gross_ticks": trade.gross_ticks,
        "cost_ticks": trade.cost_ticks,
        "mfe_ticks": trade.mfe_ticks,
        "mae_ticks": trade.mae_ticks,
        "gross_points": priced.gross_points,
        "net_points": priced.net_points,
        "commission_rub": priced.commission_rub,
        "gross_rub": priced.gross_rub,
        "net_rub": priced.net_rub,
        "step_price_estimated": priced.step_price_estimated,
        "ambiguous_bar": trade.ambiguous_bar,
        "rolled": trade.rolled,
    }


def _metrics_by_unit(
    evaluation: Evaluation, days: list[date]
) -> dict[str, dict[str, Any]]:
    return {unit: _safe(summarize(evaluation, days, unit)) for unit in UNITS}  # type: ignore[arg-type]


def _equity(evaluation: Evaluation) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for unit in ("ticks", "rub"):
        items, _ = records(evaluation.priced, unit)  # type: ignore[arg-type]
        out[unit] = [
            {"day": p.day.isoformat(), "equity": p.equity} for p in equity_curve(items)
        ]
    return out


def _step_lookup(
    session: Session, contracts: set[int], day_of: dict[datetime, date]
) -> Any:
    """Стоимость шага по дню выхода ноги: последняя известная не позже дня."""
    table: dict[int, tuple[list[date], list[float]]] = {}
    for contract_id in contracts:
        rows = list_step_prices(session, contract_id)
        table[contract_id] = (
            [r.date for r in rows],
            [float(r.step_price) for r in rows],
        )

    def lookup(contract_id: int, at: datetime) -> tuple[float, bool] | None:
        days, values = table.get(contract_id, ([], []))
        day = day_of.get(at)
        if day is None or not days:
            return None
        position = bisect_right(days, day) - 1
        if position < 0:
            return None
        return values[position], days[position] != day

    return lookup


def _load_data(
    session: Session,
    context: JobContext,
    experiment: BacktestExperiment,
    spec: StrategySpec,
) -> tuple[BacktestData, dict[str, Any]]:
    root = session.get(Root, experiment.root_id)
    if root is None:
        raise JobFailed(f"Root {experiment.root_id} не найден")
    timeframe = experiment.timeframe_code
    if timeframe not in TIMEFRAMES:
        raise JobFailed(f"Неизвестный таймфрейм {timeframe!r}")
    context.report_progress(0.05, "чтение continuous-баров")
    items = read_continuous(session, root.id, timeframe)
    if not items:
        raise JobFailed("Нет баров для бэктеста")
    bars = [BarInput.from_bar(item.bar) for item in items]
    sim_bars = [
        SimBar(
            item.bar.timestamp,
            item.bar.close_time,
            float(item.bar.open / item.factor),
            float(item.bar.high / item.factor),
            float(item.bar.low / item.factor),
            float(item.bar.close / item.factor),
            item.contract_id,
            float(item.factor),
        )
        for item in items
    ]
    rolls = [roll.rolled_at for roll in load_rolls(session, root.id)]
    names = ("levels",) if spec.source != "pattern" else PATTERN_ENGINES
    versions: dict[str, Any] = {"template": TEMPLATE_VERSION, "engines": {}}
    engine_events: list[tuple[str, Any]] = []
    for position, name in enumerate(names):
        context.report_progress(0.1 + 0.3 * position / len(names), f"движок {name}")
        outcome = advance_run(session, name, {}, bars, timeframe, root_id=root.id)
        run = session.get(EngineRun, outcome.run_id)
        assert run is not None
        versions["engines"][name] = {
            "run_id": run.id,
            "algorithm_version": run.algorithm_version,
            "params_hash": run.params_hash,
        }
        engine_events.append((name, load_events(session, run.id)))
    series = build_series(
        f"root:{root.id}:{timeframe}", bars, roll_times=rolls, atr_period=14
    )
    occurrences = collect(series, engine_events)
    day_of = {bar.close_time: bar.trading_day for bar in bars}
    data = BacktestData(
        series=series,
        sim_bars=sim_bars,
        items=occurrences,
        tick_size=float(root.tick_size),
        step_price=_step_lookup(session, {i.contract_id for i in items}, day_of),
    )
    return data, versions


def _costs(experiment: BacktestExperiment) -> Costs:
    raw = experiment.costs
    return Costs(
        half_spread_ticks=float(raw.get("half_spread_ticks", 0.0)),
        slippage_ticks=float(raw.get("slippage_ticks", 0.0)),
        commission_per_contract=float(raw.get("commission_per_contract", 0.0)),
    )


def _run_single(
    session: Session,
    experiment: BacktestExperiment,
    data: BacktestData,
    spec: StrategySpec,
    costs: Costs,
    days: list[date],
) -> dict[str, Any]:
    evaluation = evaluate(
        data,
        spec,
        experiment.period_from,
        experiment.period_to,
        costs=costs,
        contracts=experiment.contracts,
    )
    add_trades(session, experiment.id, [_trade_row(p) for p in evaluation.priced])
    return {
        "metrics": _metrics_by_unit(evaluation, days),
        "equity": _equity(evaluation),
        "signals": _safe(evaluation.built),
        "skipped": _safe(evaluation.skipped),
    }


def _walk_forward_config(experiment: BacktestExperiment) -> WalkForwardConfig:
    raw = experiment.walk_forward
    if raw is None:
        raise JobFailed("Для walk_forward нужна конфигурация окон")
    try:
        config = WalkForwardConfig(
            train_days=int(raw["train_days"]),
            valid_days=int(raw["valid_days"]),
            step_days=int(raw["step_days"]),
            grid={k: list(v) for k, v in (raw.get("grid") or {}).items()},
            objective=raw.get("objective", "profit_factor"),
            min_trades=int(raw.get("min_trades", 10)),
        )
        config.validate()
    except (KeyError, TypeError, ValueError) as error:
        raise JobFailed(f"Неверная конфигурация walk-forward: {error}") from error
    return config


def _store_windows(
    session: Session, experiment: BacktestExperiment, result: WalkForwardResult
) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for sequence, window in enumerate(result.windows):
        row = add_window(
            session,
            experiment.id,
            sequence,
            train=window.window.train,
            valid=window.window.valid,
            params=window.params or {},
            train_metrics=_safe(window.train_metrics),
            valid_metrics=_safe(window.valid_metrics),
        )
        if window.validation is not None:
            add_trades(
                session,
                experiment.id,
                [_trade_row(p) for p in window.validation.priced],
                segment="validation",
                window_id=row.id,
            )
        summary.append(
            {
                "window_id": row.id,
                "params": window.params,
                "candidates": _safe(window.candidates),
            }
        )
    return summary


def _run_walk_forward(
    session: Session,
    experiment: BacktestExperiment,
    data: BacktestData,
    spec: StrategySpec,
    costs: Costs,
    days: list[date],
) -> dict[str, Any]:
    config = _walk_forward_config(experiment)
    test_from, test_to = experiment.test_from, experiment.test_to
    if (test_from is None) != (test_to is None):
        raise JobFailed("Test-период задаётся парой дат")
    if test_from is not None and test_from <= experiment.period_to:
        raise JobFailed("Test-период должен идти строго после периода walk-forward")
    period_days = [
        d for d in days if experiment.period_from <= d <= experiment.period_to
    ]
    result = walk_forward(
        data, spec, config, period_days, costs=costs, contracts=experiment.contracts
    )
    windows = _store_windows(session, experiment, result)
    payload: dict[str, Any] = {
        "metrics": {"ticks": _safe(result.validation_metrics)},
        "walk_forward": {
            "final_params": result.final_params,
            "warnings": result.warnings,
            "windows": windows,
        },
        "test": None,
    }
    if test_from is None or test_to is None:
        return payload
    if result.final_params is None:
        payload["test"] = {"status": "skipped_no_params"}  # test не расходуем
        return payload
    attempt = open_test_period(
        session,
        experiment_id=experiment.id,
        root_id=experiment.root_id,
        timeframe_code=experiment.timeframe_code,
        family=experiment.family,
        test_from=test_from,
        test_to=test_to,
    )
    if not attempt.opened:
        payload["test"] = {
            "status": "rejected",
            "reason": "test-период этой связки уже открыт",
            "opened_by_experiment": attempt.lock.experiment_id,
            "test_from": attempt.lock.test_from.isoformat(),
            "test_to": attempt.lock.test_to.isoformat(),
        }
        return payload
    evaluation = evaluate(
        data,
        apply_params(spec, result.final_params),
        test_from,
        test_to,
        costs=costs,
        contracts=experiment.contracts,
    )
    add_trades(
        session,
        experiment.id,
        [_trade_row(p) for p in evaluation.priced],
        segment="test",
    )
    payload["test"] = {
        "status": "evaluated",
        "params": result.final_params,
        "metrics": _metrics_by_unit(evaluation, days),
        "equity": _equity(evaluation),
        "signals": _safe(evaluation.built),
        "skipped": _safe(evaluation.skipped),
    }
    return payload


def _execute(
    session: Session, context: JobContext, experiment: BacktestExperiment
) -> dict[str, Any]:
    try:
        spec = StrategySpec.from_dict(experiment.strategy)
        spec.validate()
    except ValueError as error:
        raise JobFailed(f"Неверная стратегия: {error}") from error
    data, versions = _load_data(session, context, experiment, spec)
    experiment.versions = versions
    costs = _costs(experiment)
    days = trading_days(data)
    context.report_progress(0.5, "прогон стратегии")
    if experiment.kind == "walk_forward":
        payload = _run_walk_forward(session, experiment, data, spec, costs, days)
    else:
        payload = _run_single(session, experiment, data, spec, costs, days)
    payload["runs_for_series"] = count_runs(
        session,
        root_id=experiment.root_id,
        timeframe_code=experiment.timeframe_code,
        family=experiment.family,
    )
    payload["bars"] = len(data.series.bars)
    payload["occurrences"] = len(data.items)
    return payload


def make_backtest_handler(session_factory: sessionmaker[Session]) -> Handler:
    def handler(context: JobContext) -> dict[str, Any]:
        raw = context.params.get("experiment_id")
        if not isinstance(raw, int):
            raise JobFailed("Укажите experiment_id")
        with session_factory() as session, session.begin():
            experiment = session.get(BacktestExperiment, raw)
            if experiment is None:
                raise JobFailed(f"Эксперимент {raw} не найден")
            if experiment.status in ("succeeded", "failed"):
                raise JobFailed(f"Эксперимент {raw} уже завершён")
            mark_running(session, raw)
        try:
            with session_factory() as session, session.begin():
                experiment = session.get_one(BacktestExperiment, raw)
                payload = _execute(session, context, experiment)
                finish_experiment(session, raw, payload)
        except (JobCancelled, JobInterrupted, JobLost):
            raise
        except JobFailed as error:
            _record_failure(session_factory, raw, str(error))
            raise
        except Exception as error:
            _record_failure(session_factory, raw, f"{type(error).__name__}: {error}")
            raise
        context.report_progress(1.0, "готово")
        return {"experiment_id": raw, "metrics": _headline(payload)}

    return handler


def _headline(payload: dict[str, Any]) -> dict[str, Any]:
    ticks = payload["metrics"].get("ticks") or {}
    keys = ("trades", "net", "win_rate", "profit_factor", "max_drawdown")
    return {key: ticks.get(key) for key in keys}


def _record_failure(
    session_factory: sessionmaker[Session], experiment_id: int, message: str
) -> None:
    with session_factory() as session, session.begin():
        fail_experiment(session, experiment_id, message)
