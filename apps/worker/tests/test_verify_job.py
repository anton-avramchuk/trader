"""Задача verify.indicators: online replay индикаторов на свечах из БД."""

from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker
from trader_engine.indicators import available

from tests.conftest import seed_candles
from tests.helpers import run_job
from trader_worker.runner import Worker


def test_all_registered_indicators_pass(
    loader_worker: Worker, session_factory: sessionmaker[Session], instrument_id: int
) -> None:
    seed_candles(session_factory, instrument_id, "15m", 120)

    job = run_job(
        loader_worker,
        session_factory,
        "verify.indicators",
        instrument_id=instrument_id,
        timeframe="15m",
        max_positions=40,
    )

    assert job.status == "succeeded", job.error
    result = job.result
    assert result["ok"] is True and result["bars"] == 120
    assert [r["indicator"] for r in result["reports"]] == [p.name for p in available()]
    assert all(
        r["mismatch_count"] == 0 and r["positions_checked"] <= 41
        for r in result["reports"]
    )


def test_selected_indicators_with_params(
    loader_worker: Worker, session_factory: sessionmaker[Session], instrument_id: int
) -> None:
    seed_candles(session_factory, instrument_id, "1h", 80)

    job = run_job(
        loader_worker,
        session_factory,
        "verify.indicators",
        instrument_id=instrument_id,
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
    loader_worker: Worker, session_factory: sessionmaker[Session], instrument_id: int
) -> None:
    seed_candles(session_factory, instrument_id, "1h", 80)
    common: dict[str, Any] = {
        "instrument_id": instrument_id,
        "timeframe": "1h",
        "indicators": [{"name": "sma"}],
    }
    everything = run_job(
        loader_worker, session_factory, "verify.indicators", **common
    ).result

    narrowed = run_job(
        loader_worker,
        session_factory,
        "verify.indicators",
        start=everything["range"][0],
        end="2026-01-06T00:00:00Z",
        **common,
    ).result

    assert 0 < narrowed["bars"] < everything["bars"]


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"timeframe": "15m"}, "instrument_id"),
        ({"instrument_id": 1, "timeframe": "2h"}, "Неизвестный таймфрейм"),
        (
            {"instrument_id": 1, "timeframe": "15m", "indicators": [{"name": "nope"}]},
            "nope",
        ),
        (
            {
                "instrument_id": 1,
                "timeframe": "15m",
                "indicators": [{"name": "sma", "params": {"period": 0}}],
            },
            "period",
        ),
        ({"instrument_id": 1, "timeframe": "15m", "max_positions": 0}, "max_positions"),
        ({"instrument_id": 1, "timeframe": "15m", "start": "вчера"}, "ISO 8601"),
        (
            {"instrument_id": 1, "timeframe": "15m", "start": "2026-09-28T00:00:00"},
            "часовым поясом",
        ),
        ({"instrument_id": 999_999, "timeframe": "15m"}, "не найден"),
    ],
)
def test_bad_input_gives_a_readable_error(
    loader_worker: Worker,
    session_factory: sessionmaker[Session],
    params: dict[str, Any],
    message: str,
) -> None:
    job = run_job(loader_worker, session_factory, "verify.indicators", **params)

    assert job.status == "failed"
    assert message in (job.error or "")


def test_instrument_without_candles_is_a_readable_error(
    loader_worker: Worker, session_factory: sessionmaker[Session], instrument_id: int
) -> None:
    job = run_job(
        loader_worker,
        session_factory,
        "verify.indicators",
        instrument_id=instrument_id,
        timeframe="15m",
    )

    assert job.status == "failed" and "нет баров" in (job.error or "")
