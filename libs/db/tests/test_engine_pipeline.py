"""Интеграция движков MVP-3 с PostgreSQL: прогон по частям равен полному прогону."""

import random
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session
from trader_engine.events import Event, known_at
from trader_engine.indicators import BarInput
from trader_engine.stats.pipeline import build_series, collect, compute_statistics

from trader_db import advance_run, load_events

CONFIGS: list[tuple[str, dict[str, Any]]] = [
    ("zigzag", {"threshold": "percent", "percent": 2}),
    ("swing_fixed", {"window": 3}),
    ("market_structure", {"atr_period": 3, "atr_mult": 1.0}),
    ("pivot", {}),
    ("fibonacci", {"atr_period": 3, "atr_mult": 1.0}),
    ("levels", {"atr_period": 3, "atr_mult": 1.0, "max_age": 60}),
    (
        "double_triple",
        {"atr_period": 3, "atr_mult": 1.0, "tol_atr": 3.0, "min_height_atr": 0.5},
    ),
    (
        "head_shoulders",
        {
            "atr_period": 3,
            "atr_mult": 1.0,
            "shoulder_tol_atr": 5.0,
            "head_min_atr": 0.1,
            "min_height_atr": 0.3,
        },
    ),
    (
        "trendlines",
        {"atr_period": 3, "atr_mult": 1.0, "tol_atr": 3.0, "min_height_atr": 0.5},
    ),
    (
        "range_breakout",
        {"atr_period": 3, "atr_mult": 1.0, "tol_atr": 3.0, "min_height_atr": 0.5},
    ),
]


def bars() -> list[BarInput]:
    rng = random.Random(41)
    result: list[BarInput] = []
    price = 100.0
    for index in range(6 * 32):
        opened = datetime(2026, 9, 28, 4, tzinfo=UTC) + timedelta(minutes=15 * index)
        close = round(
            max(price + rng.gauss(0, 2) + (2 if index // 20 % 2 else -2), 5), 2
        )
        result.append(
            BarInput(
                timestamp=opened,
                close_time=opened + timedelta(minutes=15),
                open=price,
                high=max(price, close) + rng.uniform(0, 1),
                low=min(price, close) - rng.uniform(0, 1),
                close=close,
                volume=1.0,
                trading_day=date(2026, 9, 28) + timedelta(days=index // 32),
            )
        )
        price = close
    return result


@pytest.mark.parametrize(("engine", "params"), CONFIGS, ids=[c[0] for c in CONFIGS])
def test_chunked_runs_equal_a_full_run(
    session: Session, contract_id: int, engine: str, params: dict[str, Any]
) -> None:
    data = bars()

    first = advance_run(
        session, engine, params, data[:50], "15m", contract_id=contract_id
    )
    second = advance_run(
        session, engine, params, data[:120], "15m", contract_id=contract_id
    )
    last = advance_run(session, engine, params, data, "15m", contract_id=contract_id)
    fresh = advance_run(
        session, engine, params, data, "15m", contract_id=contract_id, mode="full"
    )

    assert {second.run_id, last.run_id} == {first.run_id}
    assert last.outcome == "continued" and fresh.run_id != first.run_id
    continued = load_events(session, last.run_id)
    assert continued == load_events(session, fresh.run_id)
    assert continued, f"{engine}: нет событий"


@pytest.mark.parametrize(("engine", "params"), CONFIGS, ids=[c[0] for c in CONFIGS])
def test_stored_events_as_of_do_not_depend_on_later_bars(
    session: Session, contract_id: int, engine: str, params: dict[str, Any]
) -> None:
    data = bars()
    cut = 100
    moment = data[cut - 1].close_time
    other = [
        *data[:cut],
        *[replace(b, open=1.0, high=500.0, low=0.5, close=250.0) for b in data[cut:]],
    ]

    real = advance_run(session, engine, params, data, "15m", contract_id=contract_id)
    changed = advance_run(
        session, engine, params, other, "15m", contract_id=contract_id
    )

    assert real.run_id != changed.run_id  # история изменилась — новый прогон
    known = load_events(session, real.run_id, as_of=moment)
    assert known == load_events(session, changed.run_id, as_of=moment)
    assert known == known_at(load_events(session, real.run_id), moment)


STATS_ENGINES = [
    "levels",
    "double_triple",
    "head_shoulders",
    "trendlines",
    "range_breakout",
]


def stored_statistics(
    session: Session, contract_id: int, data: list[BarInput], moment: datetime | None
) -> Any:
    """Статистика по событиям, прочитанным из БД (прогоны всех движков MVP-3/4)."""
    logs: list[tuple[str, list[Event]]] = []
    for engine, params in CONFIGS:
        if engine not in STATS_ENGINES:
            continue
        run = advance_run(session, engine, params, data, "15m", contract_id=contract_id)
        logs.append((engine, load_events(session, run.run_id, as_of=moment)))
    series = build_series("db:15m", data, as_of=moment, atr_period=5)
    items = collect(series, logs)
    return items, compute_statistics(
        items, horizons=(3, 8), group_by="group", seed=1, min_baseline=5
    )


def test_statistics_from_stored_events_and_as_of(
    session: Session, contract_id: int
) -> None:
    data = bars()
    items, full = stored_statistics(session, contract_id, data, None)

    assert full.matched > 0 and len(items) >= full.matched
    assert all(item.entry_index is not None for item in items)

    cut = 100
    moment = data[cut - 1].close_time
    other = [
        *data[:cut],
        *[replace(b, open=1.0, high=500.0, low=0.5, close=250.0) for b in data[cut:]],
    ]
    _, early = stored_statistics(session, contract_id, data, moment)
    _, early_changed = stored_statistics(session, contract_id, other, moment)

    assert early == early_changed  # будущее не влияет на статистику в момент as_of
    assert early.matched < full.matched
