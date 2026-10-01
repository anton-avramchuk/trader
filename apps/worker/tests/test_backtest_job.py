"""Задача backtest.run: одиночный прогон, walk-forward, test-lock, ошибки (нужна БД)."""

from datetime import date
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from trader_db import create_experiment, list_log, list_trades, list_windows
from trader_db.models import BacktestExperiment, BacktestTestLock

from tests.test_aggregate_job import loaded_contract
from tests.test_iss_jobs import run_job
from trader_worker.runner import Worker

FROM, TO = date(2020, 1, 1), date(2030, 1, 1)
LEVEL_STRATEGY: dict[str, Any] = {
    "source": "level_touch",
    "stop": {"kind": "atr", "value": 1.0},
    "target": {"kind": "atr", "value": 2.0},
    "max_bars": 6,
}


def new_experiment(factory: sessionmaker[Session], root_id: int, **kw: Any) -> int:
    params: dict[str, Any] = {
        "kind": "single",
        "root_id": root_id,
        "timeframe_code": "15m",
        "family": "level_touch",
        "strategy": LEVEL_STRATEGY,
        "costs": {"slippage_ticks": 1, "commission_per_contract": 5},
        "period_from": FROM,
        "period_to": TO,
        "params_hash": "h",
    }
    with factory() as session:
        experiment = create_experiment(session, **(params | kw))
        session.commit()
        return experiment.id


def prepared(worker: Worker, factory: sessionmaker[Session], root_id: int) -> None:
    loaded_contract(worker, factory, root_id)
    run_job(worker, factory, "aggregate.contract", contract_id=1)


def load_experiment(
    factory: sessionmaker[Session], experiment_id: int
) -> BacktestExperiment:
    with factory() as session:
        return session.get_one(BacktestExperiment, experiment_id)


def test_single_run_stores_result_trades_and_versions(
    iss_worker: Worker, session_factory: sessionmaker[Session], root_id: int
) -> None:
    prepared(iss_worker, session_factory, root_id)
    experiment_id = new_experiment(session_factory, root_id)

    job = run_job(
        iss_worker, session_factory, "backtest.run", experiment_id=experiment_id
    )

    assert job.status == "succeeded", job.error
    assert job.result["experiment_id"] == experiment_id
    stored = load_experiment(session_factory, experiment_id)
    assert stored.status == "succeeded" and stored.finished_at is not None
    assert stored.result is not None
    assert set(stored.result["metrics"]) == {"ticks", "points", "rub"}
    assert stored.result["bars"] > 2 and stored.result["runs_for_series"] == 1
    assert set(stored.result["equity"]) == {"ticks", "rub"}
    assert stored.versions["template"] == 1
    assert stored.versions["engines"]["levels"]["algorithm_version"] >= 1
    assert stored.versions["engines"]["levels"]["run_id"] > 0
    with session_factory() as session:
        trades = list_trades(session, experiment_id)
    assert len(trades) == stored.result["metrics"]["ticks"]["trades"]
    for trade in trades:
        assert trade.segment == "single" and trade.exit_time >= trade.entry_time
        assert trade.signal_price is not None  # close сигнального бара


def test_repeated_run_is_reproducible(
    iss_worker: Worker, session_factory: sessionmaker[Session], root_id: int
) -> None:
    prepared(iss_worker, session_factory, root_id)
    first = new_experiment(session_factory, root_id)
    second = new_experiment(session_factory, root_id)

    run_job(iss_worker, session_factory, "backtest.run", experiment_id=first)
    run_job(iss_worker, session_factory, "backtest.run", experiment_id=second)

    a, b = (load_experiment(session_factory, i) for i in (first, second))
    assert a.result is not None and b.result is not None
    assert a.result["metrics"] == b.result["metrics"]
    assert a.result["equity"] == b.result["equity"]
    # журнал считает запуски с момента создания: к обоим прогонам их уже два
    assert a.result["runs_for_series"] == b.result["runs_for_series"] == 2


def test_walk_forward_with_test_opens_once(
    iss_worker: Worker, session_factory: sessionmaker[Session], root_id: int
) -> None:
    prepared(iss_worker, session_factory, root_id)
    wf = {
        "train_days": 1,
        "valid_days": 1,
        "step_days": 1,
        "grid": {"stop_atr": [0.5, 1.0], "target_atr": [1.0]},
        "min_trades": 1,
    }
    common: dict[str, Any] = {
        "kind": "walk_forward",
        "walk_forward": wf,
        "period_to": date(2026, 9, 29),
        "test_from": date(2026, 9, 30),
        "test_to": date(2026, 10, 30),
    }
    first = new_experiment(session_factory, root_id, **common)
    second = new_experiment(session_factory, root_id, **common)

    job = run_job(iss_worker, session_factory, "backtest.run", experiment_id=first)
    again = run_job(iss_worker, session_factory, "backtest.run", experiment_id=second)

    assert job.status == "succeeded", job.error
    result = load_experiment(session_factory, first).result
    assert result is not None and result["walk_forward"]["windows"] is not None
    with session_factory() as session:
        windows = list_windows(session, first)
        events = [e.event for e in list_log(session, root_id=root_id)]
        locks = session.scalars(select(BacktestTestLock)).all()
    assert len(windows) == len(result["walk_forward"]["windows"])
    test = result["test"]
    assert test["status"] in ("evaluated", "skipped_no_params")
    if test["status"] == "evaluated":
        assert len(locks) == 1 and "test_opened" in events
        repeat = load_experiment(session_factory, second).result
        assert again.status == "succeeded"
        assert repeat is not None and repeat["test"]["status"] == "rejected"
        assert "test_rejected" in events
        with session_factory() as session:
            assert list_trades(session, second, segment="test") == []


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"strategy": {"source": "nonsense"}}, "Неверная стратегия"),
        ({"kind": "walk_forward"}, "конфигурац"),
        (
            {
                "kind": "walk_forward",
                "walk_forward": {"train_days": 0, "valid_days": 1, "step_days": 1},
            },
            "walk-forward",
        ),
        (
            {
                "kind": "walk_forward",
                "walk_forward": {"train_days": 2, "valid_days": 1, "step_days": 1},
                "test_from": date(2020, 6, 1),
                "test_to": date(2020, 7, 1),
                "period_to": date(2020, 12, 31),
            },
            "после периода",
        ),
    ],
)
def test_bad_experiment_fails_with_a_readable_error(
    iss_worker: Worker,
    session_factory: sessionmaker[Session],
    root_id: int,
    kw: dict[str, Any],
    message: str,
) -> None:
    prepared(iss_worker, session_factory, root_id)
    experiment_id = new_experiment(session_factory, root_id, **kw)

    job = run_job(
        iss_worker, session_factory, "backtest.run", experiment_id=experiment_id
    )

    assert job.status == "failed" and message in (job.error or "")
    stored = load_experiment(session_factory, experiment_id)
    assert stored.status == "failed" and message in (stored.error or "")
    with session_factory() as session:
        assert "failed" in [e.event for e in list_log(session, root_id=root_id)]


def test_missing_experiment_or_parameter(
    iss_worker: Worker, session_factory: sessionmaker[Session], root_id: int
) -> None:
    missing = run_job(iss_worker, session_factory, "backtest.run", experiment_id=999999)
    no_param = run_job(iss_worker, session_factory, "backtest.run")

    assert missing.status == "failed" and "не найден" in (missing.error or "")
    assert no_param.status == "failed" and "experiment_id" in (no_param.error or "")


def test_finished_experiment_is_not_rerun(
    iss_worker: Worker, session_factory: sessionmaker[Session], root_id: int
) -> None:
    prepared(iss_worker, session_factory, root_id)
    experiment_id = new_experiment(session_factory, root_id)
    run_job(iss_worker, session_factory, "backtest.run", experiment_id=experiment_id)

    again = run_job(
        iss_worker, session_factory, "backtest.run", experiment_id=experiment_id
    )

    assert again.status == "failed" and "уже завершён" in (again.error or "")
