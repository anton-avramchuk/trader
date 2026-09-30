"""Задача verify.indicators: online replay индикаторов на барах из БД."""

from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker
from trader_engine.indicators import available

from tests.test_aggregate_job import loaded_contract
from tests.test_iss_jobs import run_job
from trader_worker.runner import Worker


def prepared(iss_worker: Worker, factory: sessionmaker[Session], root_id: int) -> int:
    contract_id = loaded_contract(iss_worker, factory, root_id)
    run_job(iss_worker, factory, "aggregate.contract", contract_id=contract_id)
    return contract_id


def test_all_registered_indicators_pass_on_real_bars(
    iss_worker: Worker, session_factory: sessionmaker[Session], root_id: int
) -> None:
    contract_id = prepared(iss_worker, session_factory, root_id)

    job = run_job(
        iss_worker,
        session_factory,
        "verify.indicators",
        contract_id=contract_id,
        timeframe="15m",
        max_positions=40,
    )

    assert job.status == "succeeded", job.error
    result = job.result
    assert result["ok"] is True and result["bars"] > 5
    names = [r["indicator"] for r in result["reports"]]
    assert names == [p.name for p in available()]
    assert all(
        r["mismatch_count"] == 0 and r["positions_checked"] <= 41
        for r in result["reports"]
    )


def test_selected_indicators_with_params_on_continuous_series(
    iss_worker: Worker, session_factory: sessionmaker[Session], root_id: int
) -> None:
    prepared(iss_worker, session_factory, root_id)

    job = run_job(
        iss_worker,
        session_factory,
        "verify.indicators",
        root_id=root_id,
        timeframe="1h",
        indicators=[{"name": "ema", "params": {"period": 3}}, {"name": "macd"}],
    )

    assert job.status == "succeeded", job.error
    assert [(r["indicator"], r["params"]) for r in job.result["reports"]] == [
        ("ema", {"period": 3}),
        ("macd", {}),
    ]
    assert job.result["range"][0] < job.result["range"][1]


def test_range_limits_the_bars(
    iss_worker: Worker, session_factory: sessionmaker[Session], root_id: int
) -> None:
    contract_id = prepared(iss_worker, session_factory, root_id)
    everything = run_job(
        iss_worker,
        session_factory,
        "verify.indicators",
        contract_id=contract_id,
        timeframe="15m",
        indicators=[{"name": "sma"}],
    ).result

    narrowed = run_job(
        iss_worker,
        session_factory,
        "verify.indicators",
        contract_id=contract_id,
        timeframe="15m",
        indicators=[{"name": "sma"}],
        start=everything["range"][0],
        end="2026-09-29T00:00:00Z",
    ).result

    assert 0 < narrowed["bars"] < everything["bars"]


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"timeframe": "15m"}, "root_id или contract_id"),
        (
            {"timeframe": "15m", "root_id": 1, "contract_id": 1},
            "root_id или contract_id",
        ),
        ({"contract_id": 1, "timeframe": "2h"}, "Неизвестный таймфрейм"),
        (
            {"contract_id": 1, "timeframe": "15m", "indicators": [{"name": "nope"}]},
            "nope",
        ),
        (
            {
                "contract_id": 1,
                "timeframe": "15m",
                "indicators": [{"name": "sma", "params": {"period": 0}}],
            },
            "period",
        ),
        ({"contract_id": 1, "timeframe": "15m", "max_positions": 0}, "max_positions"),
        ({"contract_id": 1, "timeframe": "15m", "start": "вчера"}, "ISO 8601"),
        (
            {"contract_id": 1, "timeframe": "15m", "start": "2026-09-28T00:00:00"},
            "часовым поясом",
        ),
        ({"contract_id": 999_999, "timeframe": "15m"}, "не найден"),
        ({"root_id": 999_999, "timeframe": "15m"}, "не найден"),
    ],
)
def test_bad_input_gives_a_readable_error(
    iss_worker: Worker,
    session_factory: sessionmaker[Session],
    params: dict[str, Any],
    message: str,
) -> None:
    job = run_job(iss_worker, session_factory, "verify.indicators", **params)

    assert job.status == "failed"
    assert message in (job.error or "")


def test_contract_without_bars_is_a_readable_error(
    iss_worker: Worker, session_factory: sessionmaker[Session], root_id: int
) -> None:
    run_job(
        iss_worker,
        session_factory,
        "iss.sync_root",
        root_id=root_id,
        enqueue_imports=False,
    )
    from tests.test_iss_jobs import contract_id_of

    contract_id = contract_id_of(session_factory, "BRZ6")

    job = run_job(
        iss_worker,
        session_factory,
        "verify.indicators",
        contract_id=contract_id,
        timeframe="15m",
    )

    assert job.status == "failed" and "нет баров" in (job.error or "")
