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


def test_grid_follows_the_leg_and_restarts_on_new_swing() -> None:
    bars = closes(SERIES)

    events = run_engine(create("fibonacci", PERCENT), bars)

    assert legs(events)[:6] == [
        ("detected", 120, 105),
        ("revised", 120, 100),
        ("detected", 100, 110),
        ("revised", 100, 130),
        ("detected", 130, 115),
        ("revised", 130, 105),
    ]
    assert events[0].available_at == bars[3].close_time
    assert events[1].revises == events[0].seq and events[2].revises is None
    assert (
        events[0].payload["direction"] == "down"
        and events[2].payload["direction"] == "up"
    )
    assert events[0].payload["start"]["timestamp"] == bars[2].timestamp.isoformat()


def test_current_picture_is_the_latest_grid_only_per_leg() -> None:
    events = run_engine(create("fibonacci", PERCENT), closes(SERIES))

    current = current_events(events)

    assert legs(current)[:3] == [
        ("revised", 120, 100),
        ("revised", 100, 130),
        ("revised", 130, 105),
    ]


def test_as_of_hides_later_grids() -> None:
    bars = closes(SERIES)
    events = run_engine(create("fibonacci", PERCENT), bars)

    known = known_at(events, bars[4].close_time)

    assert legs(current_events(known)) == [("revised", 120, 100)]


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
