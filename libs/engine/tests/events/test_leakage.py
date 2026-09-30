"""Leakage-тесты и online replay движков MVP-3 (spec §42, §55–58).

Каждый движок обязан выполнять контракт доступности: событие с ``available_at = t``
не зависит ни от одного бара, закрытого после ``t``; ничего не «знает» о будущем.
Проверка — подменой будущего на другие данные и сравнением известных к моменту событий.
"""

import json
from datetime import datetime
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.events.dataset import fixed_bars, perturb_future
from tests.indicators.helpers import bar_series
from trader_engine.events import Event, create, known_at, run_engine
from trader_engine.indicators import BarInput

PERCENT: dict[str, Any] = {"threshold": "percent", "percent": 2}
FAST_ATR: dict[str, Any] = {"atr_period": 3, "atr_mult": 1.0}

CONFIGS: list[tuple[str, dict[str, Any]]] = [
    ("zigzag", {}),
    ("zigzag", PERCENT),
    ("swing_fixed", {"window": 3}),
    ("market_structure", FAST_ATR),
    ("market_structure", PERCENT),
    ("pivot", {}),
    ("pivot", {"formula": "camarilla", "weekly": False}),
    ("fibonacci", FAST_ATR),
    ("fibonacci", PERCENT),
    ("levels", FAST_ATR | {"touch_k": 0.5, "max_age": 60}),
    ("levels", PERCENT | {"atr_period": 5}),
    ("double_triple", FAST_ATR | {"tol_atr": 3.0, "min_height_atr": 0.5}),
    (
        "head_shoulders",
        FAST_ATR
        | {"shoulder_tol_atr": 5.0, "head_min_atr": 0.1, "min_height_atr": 0.3},
    ),
    ("trendlines", FAST_ATR | {"tol_atr": 3.0, "min_height_atr": 0.5}),
    ("range_breakout", FAST_ATR | {"tol_atr": 3.0, "min_height_atr": 0.5}),
]
IDS = [f"{name}-{i}" for i, (name, _) in enumerate(CONFIGS)]
ENGINES = pytest.mark.parametrize(("name", "params"), CONFIGS, ids=IDS)


def signature(events: list[Event]) -> list[Any]:
    return [
        (
            e.seq,
            e.kind,
            e.status,
            e.available_at,
            e.revises,
            json.dumps(e.payload, sort_keys=True),
        )
        for e in events
    ]


@ENGINES
class TestAvailabilityContract:
    def test_events_are_stamped_with_a_closing_bar(
        self, name: str, params: dict[str, Any]
    ) -> None:
        bars = fixed_bars()
        events = run_engine(create(name, params), bars)

        closes = {bar.close_time for bar in bars}
        assert events, f"{name}: на фиксированном датасете нет событий"
        assert all(e.available_at in closes for e in events)
        assert [e.available_at for e in events] == sorted(
            e.available_at for e in events
        )
        for event in events:
            assert event.detected_at <= event.available_at
            if event.confirmed_at is not None:
                assert event.detected_at <= event.confirmed_at <= event.available_at
            stamp = event.payload.get("timestamp")
            if stamp is not None:  # бар-экстремум не может быть позже знания о нём
                assert datetime.fromisoformat(stamp) < event.available_at

    def test_future_replaced_by_other_data_changes_nothing_known(
        self, name: str, params: dict[str, Any]
    ) -> None:
        bars = fixed_bars()
        baseline = run_engine(create(name, params), bars)

        for cut in (40, 97, 130, 200):
            moment = bars[cut - 1].close_time
            changed = run_engine(create(name, params), perturb_future(bars, cut))

            assert signature(known_at(changed, moment)) == signature(
                known_at(baseline, moment)
            ), f"{name}: события на {moment} зависят от будущих баров"
            assert changed != baseline  # подмена реально повлияла на будущее

    def test_online_replay_bar_by_bar_equals_batch(
        self, name: str, params: dict[str, Any]
    ) -> None:
        bars = fixed_bars()
        batch = run_engine(create(name, params), bars)

        engine = create(name, params)
        replay: list[Event] = []
        for bar in bars:
            replay += engine.update(bar)

        assert replay == batch

    def test_restart_from_saved_state_at_every_stride(
        self, name: str, params: dict[str, Any]
    ) -> None:
        bars = fixed_bars()
        batch = run_engine(create(name, params), bars)

        for cut in range(1, len(bars), 37):
            head_engine = create(name, params)
            head = run_engine(head_engine, bars[:cut])
            tail_engine = create(name, params)
            tail_engine.load_state(json.loads(json.dumps(head_engine.dump_state())))

            assert head + run_engine(tail_engine, bars[cut:]) == batch, (
                f"{name}, cut={cut}"
            )


@ENGINES
class TestGeneratedSeries:
    @given(data=st.data(), bars=bar_series(min_size=8, max_size=100))
    @settings(max_examples=30, deadline=None)
    def test_perturbing_the_future_is_invisible(
        self,
        name: str,
        params: dict[str, Any],
        data: st.DataObject,
        bars: list[BarInput],
    ) -> None:
        cut = data.draw(st.integers(min_value=1, max_value=len(bars) - 1))
        moment = bars[cut - 1].close_time

        real = known_at(run_engine(create(name, params), bars), moment)
        other = known_at(
            run_engine(create(name, params), perturb_future(bars, cut)), moment
        )

        assert signature(real) == signature(other)
