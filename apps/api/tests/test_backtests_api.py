"""REST бэктестов: запуск, валидация, результаты, журнал, блокировки (нужна БД)."""

from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from trader_db import (
    add_trades,
    add_window,
    finish_experiment,
    make_engine,
    open_test_period,
)

from tests.seeding import Seed

NOON = datetime(2026, 10, 1, 12, tzinfo=UTC)


def body(seed: Seed, **kw: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "instrument_id": seed.id,
        "timeframe": "15m",
        "strategy": {"source": "level_touch", "max_bars": 6},
        "costs": {"slippage_ticks": 1},
        "quantity": 2,
        "period_from": "2026-09-28",
        "period_to": "2026-10-06",
    }
    return payload | kw


def create(client: TestClient, seed: Seed, **kw: Any) -> dict[str, Any]:
    response = client.post("/backtests", json=body(seed, **kw))
    assert response.status_code == 201, response.text
    return response.json()


def test_create_stores_experiment_and_queues_the_job(
    client: TestClient, seed: Seed
) -> None:
    created = create(client, seed)

    assert created["status"] == "queued" and created["kind"] == "single"
    assert created["family"] == "level_touch" and created["quantity"] == 2
    assert created["strategy"]["source"] == "level_touch"
    assert created["strategy"]["stop"] == {"kind": "atr", "value": 1.5}
    assert len(created["params_hash"]) == 64 and created["job_id"] is not None
    job = client.get(f"/jobs/{created['job_id']}").json()
    assert job["type"] == "backtest.run"
    assert job["params"] == {"experiment_id": created["id"]}
    assert client.get(f"/backtests/{created['id']}").json()["id"] == created["id"]


def test_same_inputs_give_the_same_hash_and_each_run_is_logged(
    client: TestClient, seed: Seed
) -> None:
    first, second = create(client, seed), create(client, seed)
    other = create(client, seed, quantity=3)

    assert first["params_hash"] == second["params_hash"] != other["params_hash"]
    log = client.get(
        "/backtest-log", params={"instrument_id": seed.id, "family": "level_touch"}
    ).json()
    assert [entry["event"] for entry in log] == ["run", "run", "run"]
    assert log[0]["experiment_id"] == other["id"]


def test_walk_forward_request_is_stored_with_test_period(
    client: TestClient, seed: Seed
) -> None:
    created = create(
        client,
        seed,
        kind="walk_forward",
        walk_forward={
            "train_days": 3,
            "valid_days": 1,
            "step_days": 1,
            "grid": {"stop_atr": [1, 2], "target_atr": [2, 3]},
        },
        test_from="2026-10-07",
        test_to="2026-10-30",
    )

    assert created["walk_forward"]["grid"] == {
        "stop_atr": [1.0, 2.0],
        "target_atr": [2.0, 3.0],
    }
    assert created["test_from"] == "2026-10-07"
    log = client.get("/backtest-log", params={"instrument_id": seed.id}).json()
    assert log[0]["event"] == "walk_forward" and log[0]["touches_test"]


@pytest.mark.parametrize(
    "override",
    [
        {"timeframe": "2h"},
        {"strategy": {"source": "pattern", "stop": {"kind": "weird"}}},
        {"strategy": {"source": "level_touch", "stop": {"kind": "structure"}}},
        {
            "strategy": {
                "source": "pattern",
                "stop": {"kind": "none"},
                "target": {"kind": "none"},
            }
        },
        {"period_from": "2026-12-01"},
        {"quantity": 0},
        {"costs": {"slippage_ticks": -1}},
        {"kind": "walk_forward"},
        {"test_from": "2026-10-07", "test_to": "2026-10-30"},  # test — не для single
        {
            "kind": "walk_forward",
            "walk_forward": {"train_days": 2, "valid_days": 1, "step_days": 1},
            "test_from": "2026-10-07",
        },  # половина пары
        {
            "kind": "walk_forward",
            "walk_forward": {"train_days": 2, "valid_days": 1, "step_days": 1},
            "test_from": "2026-10-01",
            "test_to": "2026-10-30",
        },  # внутри периода
        {
            "kind": "walk_forward",
            "walk_forward": {
                "train_days": 2,
                "valid_days": 1,
                "step_days": 1,
                "grid": {"bogus": [1]},
            },
        },
        {
            "kind": "walk_forward",
            "walk_forward": {
                "train_days": 2,
                "valid_days": 1,
                "step_days": 1,
                "grid": {"stop_atr": []},
            },
        },
        {
            "kind": "walk_forward",
            "walk_forward": {
                "train_days": 2,
                "valid_days": 1,
                "step_days": 1,
                "grid": {
                    "stop_atr": list(range(1, 13)),
                    "target_atr": list(range(1, 13)),
                    "max_bars": list(range(1, 13)),
                },
            },
        },
    ],
)
def test_validation_errors(
    client: TestClient, seed: Seed, override: dict[str, Any]
) -> None:
    response = client.post("/backtests", json=body(seed, **override))

    assert response.status_code == 422, response.text
    assert client.get("/backtest-log").json() == []  # неверный запрос не логируется


def test_unknown_instrument_and_missing_backtest(
    client: TestClient, seed: Seed
) -> None:
    missing_instrument = client.post(
        "/backtests", json=body(seed, instrument_id=999999)
    )

    assert missing_instrument.status_code == 404
    for path in ("", "/trades", "/windows"):
        assert client.get(f"/backtests/999999{path}").status_code == 404


def fabricate(database_url: str, experiment_id: int) -> int:
    """Результат, который в проде пишет worker: окно, сделки и итог."""
    engine = make_engine(database_url)
    with Session(engine) as session:
        window = add_window(
            session,
            experiment_id,
            0,
            train=(date(2026, 9, 28), date(2026, 9, 30)),
            valid=(date(2026, 10, 1), date(2026, 10, 1)),
            params={"stop_atr": 1.0},
            train_metrics={"trades": 3},
        )
        row: dict[str, Any] = {
            "side": "long",
            "ref": "levels:1:3",
            "signal_price": 99.5,
            "quantity": 2,
            "entry_time": NOON,
            "exit_time": NOON,
            "reason": "target",
            "entry_price": 100.0,
            "exit_price": 100.05,
            "gross_ticks": 5.0,
            "cost_ticks": 2.0,
            "mfe_ticks": 6.0,
            "mae_ticks": -1.0,
            "gross_points": 0.1,
            "net_points": 0.06,
            "commission": 10.0,
            "gross_money": 100.0,
            "net_money": 50.0,
        }
        add_trades(session, experiment_id, [row, row | {"reason": "stop"}])
        add_trades(
            session, experiment_id, [row], segment="validation", window_id=window.id
        )
        finish_experiment(session, experiment_id, {"metrics": {"ticks": {"trades": 2}}})
        session.commit()
        window_id = window.id
    engine.dispose()
    return window_id


def test_results_trades_and_windows(
    client: TestClient, seed: Seed, database_url: str
) -> None:
    created = create(client, seed)
    window_id = fabricate(database_url, created["id"])
    path = f"/backtests/{created['id']}"

    done = client.get(path).json()
    assert done["status"] == "succeeded"
    assert done["result"] == {"metrics": {"ticks": {"trades": 2}}}
    trades = client.get(f"{path}/trades").json()
    assert [(t["segment"], t["sequence"]) for t in trades] == [
        ("single", 0),
        ("single", 1),
        ("validation", 0),
    ]
    assert trades[0]["net_money"] == 50.0 and trades[0]["entry_price"] == 100.0
    assert trades[0]["signal_price"] == 99.5
    assert len(client.get(f"{path}/trades", params={"segment": "single"}).json()) == 2
    assert (
        len(client.get(f"{path}/trades", params={"window_id": window_id}).json()) == 1
    )
    assert (
        len(client.get(f"{path}/trades", params={"limit": 1, "offset": 1}).json()) == 1
    )
    [window] = client.get(f"{path}/windows").json()
    assert window["params"] == {"stop_atr": 1.0} and window["valid_metrics"] is None
    listed = client.get(
        "/backtests", params={"instrument_id": seed.id, "status": "succeeded"}
    )
    assert [b["id"] for b in listed.json()] == [created["id"]]
    assert client.get("/backtests", params={"status": "failed"}).json() == []


def test_locks_listing_and_explicit_unlock(
    client: TestClient, seed: Seed, database_url: str
) -> None:
    created = create(client, seed)
    engine = make_engine(database_url)
    with Session(engine) as session:
        open_test_period(
            session,
            experiment_id=created["id"],
            instrument_id=seed.id,
            timeframe_code="15m",
            family="level_touch",
            test_from=date(2026, 10, 7),
            test_to=date(2026, 10, 30),
        )
        session.commit()
    engine.dispose()

    [lock] = client.get("/backtest-locks", params={"instrument_id": seed.id}).json()
    assert lock["family"] == "level_touch" and lock["test_from"] == "2026-10-07"
    unlock = {"instrument_id": seed.id, "timeframe": "15m", "family": "level_touch"}

    short = client.post("/backtest-locks/unlock", json=unlock | {"note": "ок"})
    done = client.post(
        "/backtest-locks/unlock", json=unlock | {"note": "ошибка в данных"}
    )
    again = client.post("/backtest-locks/unlock", json=unlock | {"note": "ещё раз"})

    assert short.status_code == 422  # причина обязательна
    assert done.json() == {"unlocked": True} and again.json() == {"unlocked": False}
    assert client.get("/backtest-locks").json() == []
    log = client.get("/backtest-log", params={"family": "level_touch"}).json()
    unlocked = next(e for e in log if e["event"] == "unlock")
    assert unlocked["note"] == "ошибка в данных" and unlocked["touches_test"]
