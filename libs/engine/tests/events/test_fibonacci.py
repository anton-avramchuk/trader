"""Fibonacci Engine: автоматическая сетка по ноге ZigZag."""

import json
from typing import Any

import pytest
from hypothesis import given, settings

from tests.indicators.helpers import bar_series, closes
from trader_engine.events import Event, create, current_events, known_at, run_engine
from trader_engine.events.fibonacci import fib_levels
from trader_engine.indicators import BarInput

PERCENT: dict[str, Any] = {"threshold": "percent", "percent": 10}
SERIES = [100.0, 110, 120, 105, 100, 110, 130, 115, 105, 116, 100, 85, 96]


def legs(events: list[Event]) -> list[tuple[str, float, float]]:
    return [
        (e.status, e.payload["start"]["price"], e.payload["end"]["price"])
        for e in events
    ]


def test_levels_of_a_down_leg() -> None:
    got = fib_levels(120.0, 100.0)

    assert got["retracement"]["38.2"] == pytest.approx(107.64)
    assert got["retracement"]["50"] == pytest.approx(110.0)
    assert got["extension"]["100"] == pytest.approx(100.0)
    assert got["extension"]["161.8"] == pytest.approx(87.64)
    assert list(got["retracement"]) == ["23.6", "38.2", "50", "61.8", "78.6"]
    assert list(got["extension"]) == ["100", "127.2", "161.8"]


def test_levels_of_an_up_leg() -> None:
    got = fib_levels(100.0, 130.0)

    assert got["retracement"]["61.8"] == pytest.approx(111.46)
    assert got["extension"]["127.2"] == pytest.approx(138.16)


def test_grid_is_built_on_confirmed_swings_not_on_the_running_leg() -> None:
    bars = closes(SERIES)

    events = run_engine(create("fibonacci", PERCENT), bars)

    # нога появляется, только когда подтверждены оба её конца; колено «в процессе»
    # (кандидат ZigZag) сетку не двигает
    assert legs(events) == [
        ("detected", 120, 100),
        ("detected", 100, 130),
        ("detected", 130, 85),
    ]
    assert events[0].available_at == bars[5].close_time
    assert all(e.revises is None for e in events)
    assert events[0].payload["direction"] == "down"
    assert events[1].payload["direction"] == "up"
    assert events[0].payload["start"]["timestamp"] == bars[2].timestamp.isoformat()


def test_small_swings_after_a_big_leg_extend_it_instead_of_new_grids() -> None:
    series = [100.0, 110, 120, 105, 100, 110, 130, 127, 131, 128, 132, 129]

    events = run_engine(create("fibonacci", {**PERCENT, "percent": 2}), closes(series))

    assert legs(events) == [
        ("detected", 120, 100),
        ("detected", 100, 130),
        ("revised", 100, 131),
        ("revised", 100, 132),
    ]


def test_leg_shorter_than_min_leg_mult_gives_no_grid() -> None:
    series = [100.0, 103, 100, 103, 100, 103, 100, 103]

    events = run_engine(create("fibonacci", {**PERCENT, "percent": 2}), closes(series))

    assert events == []


def test_current_picture_is_the_latest_grid_per_chain() -> None:
    events = run_engine(create("fibonacci", PERCENT), closes(SERIES))

    assert legs(current_events(events)) == legs(events)


def test_as_of_hides_later_grids() -> None:
    bars = closes(SERIES)
    events = run_engine(create("fibonacci", PERCENT), bars)

    known = known_at(events, bars[6].close_time)

    assert legs(current_events(known)) == [("detected", 120, 100)]


def test_no_grid_before_first_confirmed_swing() -> None:
    assert run_engine(create("fibonacci", PERCENT), closes(SERIES[:3])) == []


@pytest.mark.parametrize("params", [{}, PERCENT, {"atr_period": 3, "atr_mult": 1.0}])
class TestContract:
    @given(bars=bar_series(min_size=2, max_size=100))
    @settings(max_examples=40, deadline=None)
    def test_prefix_invariance(
        self, params: dict[str, Any], bars: list[BarInput]
    ) -> None:
        full = run_engine(create("fibonacci", params), bars)
        prefix = run_engine(create("fibonacci", params), bars[: max(len(bars) // 2, 1)])

        assert full[: len(prefix)] == prefix

    @given(bars=bar_series(min_size=4, max_size=100))
    @settings(max_examples=40, deadline=None)
    def test_state_roundtrip(
        self, params: dict[str, Any], bars: list[BarInput]
    ) -> None:
        full = run_engine(create("fibonacci", params), bars)
        cut = len(bars) // 2
        first = create("fibonacci", params)
        head = run_engine(first, bars[:cut])
        second = create("fibonacci", params)
        second.load_state(json.loads(json.dumps(first.dump_state())))

        assert head + run_engine(second, bars[cut:]) == full
