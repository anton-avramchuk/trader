"""Задача ``backtest.run``: бэктест по свечам инструмента (ADR-0027, ADR-0028).

Параметр один — ``experiment_id`` созданного API эксперимента: все входы (стратегия,
издержки, период, walk-forward, test) лежат в его строке. Задача читает свечи
инструмента, прогоняет нужные движки событий (как ``engine.run``), собирает
``BacktestData`` и выполняет прогон: одиночный период либо walk-forward с финальным
test-периодом. Test открывается один раз на связку (инструмент, TF, семейство): повтор
отклоняется и пишется в журнал. Сделки, окна и метрики сохраняются в БД.
"""

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
    get_instrument,
    load_events,
    mark_running,
    open_test_period,
    read_bars,
)
from trader_db.models import BacktestExperiment, EngineRun
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
from trader_engine.timeframes import TIMEFRAMES

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
UNITS: tuple[str, ...] = ("ticks", "points", "money")
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
        "signal_price": trade.signal_price,
        "quantity": trade.quantity,
        "entry_time": trade.entry_time,
        "exit_time": priced.exit_time,
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "reason": trade.reason,
        "gross_ticks": trade.gross_ticks,
        "cost_ticks": trade.cost_ticks,
        "mfe_ticks": trade.mfe_ticks,
        "mae_ticks": trade.mae_ticks,
        "gross_points": priced.gross_points,
        "net_points": priced.net_points,
        "commission": priced.commission,
        "gross_money": priced.gross_money,
        "net_money": priced.net_money,
        "ambiguous_bar": trade.ambiguous_bar,
    }


def _metrics_by_unit(
    evaluation: Evaluation, days: list[date]
) -> dict[str, dict[str, Any]]:
    return {unit: _safe(summarize(evaluation, days, unit)) for unit in UNITS}  # type: ignore[arg-type]


def _equity(evaluation: Evaluation) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for unit in ("ticks", "money"):
        items, _ = records(evaluation.priced, unit)  # type: ignore[arg-type]
        out[unit] = [
            {"day": p.day.isoformat(), "equity": p.equity} for p in equity_curve(items)
        ]
    return out


def _load_data(
    session: Session,
    context: JobContext,
    experiment: BacktestExperiment,
    spec: StrategySpec,
) -> tuple[BacktestData, dict[str, Any]]:
    instrument = get_instrument(session, experiment.instrument_id)
    if instrument is None:
        raise JobFailed(f"Инструмент {experiment.instrument_id} не найден")
    timeframe = experiment.timeframe_code
    if timeframe not in TIMEFRAMES:
        raise JobFailed(f"Неизвестный таймфрейм {timeframe!r}")
    context.report_progress(0.05, "чтение свечей")
    candles = read_bars(session, instrument.id, timeframe)
    if not candles:
        raise JobFailed("Нет свечей для бэктеста: сначала загрузите историю")
    bars = [BarInput.from_bar(bar) for bar in candles]
    sim_bars = [
        SimBar(
            bar.timestamp,
            bar.close_time,
            float(bar.open),
            float(bar.high),
            float(bar.low),
            float(bar.close),
        )
        for bar in candles
    ]
    # levels всегда: по ним в drill-down сделки показывается ближайший уровень
    names = ("levels", *(PATTERN_ENGINES if spec.source == "pattern" else ()))
    versions: dict[str, Any] = {
        "template": TEMPLATE_VERSION,
        "engines": {},
        "candles": {
            "count": len(candles),
            "first": candles[0].timestamp.isoformat(),
            "last": candles[-1].close_time.isoformat(),
        },
    }
    engine_events: list[tuple[str, Any]] = []
    for position, name in enumerate(names):
        context.report_progress(0.1 + 0.3 * position / len(names), f"движок {name}")
        outcome = advance_run(
            session, name, {}, bars, timeframe, instrument_id=instrument.id
        )
        run = session.get(EngineRun, outcome.run_id)
        assert run is not None
        versions["engines"][name] = {
            "run_id": run.id,
            "algorithm_version": run.algorithm_version,
            "params_hash": run.params_hash,
        }
        engine_events.append((name, load_events(session, run.id)))
    series = build_series(f"{instrument.ticker}:{timeframe}", bars, atr_period=14)
    occurrences = collect(series, engine_events)
    data = BacktestData(
        series=series,
        sim_bars=sim_bars,
        items=occurrences,
        tick_size=float(instrument.tick_size),
        tick_value=None
        if instrument.tick_value is None
        else float(instrument.tick_value),
    )
    return data, versions


def _costs(experiment: BacktestExperiment) -> Costs:
    raw = experiment.costs
    return Costs(
        half_spread_ticks=float(raw.get("half_spread_ticks", 0.0)),
        slippage_ticks=float(raw.get("slippage_ticks", 0.0)),
        commission_per_unit=float(raw.get("commission_per_unit", 0.0)),
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
        quantity=experiment.quantity,
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
        data, spec, config, period_days, costs=costs, quantity=experiment.quantity
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
        instrument_id=experiment.instrument_id,
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
        quantity=experiment.quantity,
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
        instrument_id=experiment.instrument_id,
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
