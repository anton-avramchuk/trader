"""Market structure: метки HH/HL/LH/LL и состояние тренда."""

import json
from typing import Any

import pytest
from hypothesis import given, settings

from tests.indicators.helpers import bar_series, closes
from trader_engine.events import Event, create, current_events, known_at, run_engine
from trader_engine.indicators import BarInput

PERCENT: dict[str, Any] = {"threshold": "percent", "percent": 10}
SERIES = [100.0, 110, 120, 105, 100, 110, 130, 115, 105, 116, 100, 85, 96]


def points(events: list[Event]) -> list[tuple[str, float]]:
    return [
        (e.payload["label"], e.payload["price"])
        for e in events
        if e.kind == "structure_point"
    ]


def states(events: list[Event]) -> list[str]:
    return [e.payload["state"] for e in events if e.kind == "trend_state"]


def test_labels_and_trend_states() -> None:
    events = run_engine(create("market_structure", PERCENT), closes(SERIES))

    assert points(events) == [("HH", 130), ("HL", 105), ("LH", 116), ("LL", 85)]
    assert states(events) == ["uptrend", "range", "downtrend"]
    first_state = next(e for e in events if e.kind == "trend_state")
    assert first_state.payload == {
        "state": "uptrend",
        "previous": None,
        "trigger": "HL",
    }


def test_known_only_after_swing_is_confirmed() -> None:
    bars = closes(SERIES)
    events = run_engine(create("market_structure", PERCENT), bars)

    hh = next(e for e in events if e.payload.get("label") == "HH")

    assert hh.available_at == bars[7].close_time  # закрытие подтверждающего бара
    assert hh.payload["timestamp"] == bars[6].timestamp.isoformat()
    assert points(known_at(events, bars[6].close_time)) == []
    assert states(current_events(known_at(events, bars[9].close_time))) == ["uptrend"]


def test_no_labels_before_two_swings_of_each_type() -> None:
    events = run_engine(create("market_structure", PERCENT), closes(SERIES[:6]))

    assert events == []


@pytest.mark.parametrize("params", [{}, PERCENT, {"atr_period": 3, "atr_mult": 1.0}])
class TestContract:
    @given(bars=bar_series(min_size=2, max_size=100))
    @settings(max_examples=40, deadline=None)
    def test_prefix_invariance(
        self, params: dict[str, Any], bars: list[BarInput]
    ) -> None:
        full = run_engine(create("market_structure", params), bars)
        cut = len(bars) // 2

        prefix = run_engine(create("market_structure", params), bars[: max(cut, 1)])

        assert full[: len(prefix)] == prefix

    @given(bars=bar_series(min_size=4, max_size=100))
    @settings(max_examples=40, deadline=None)
    def test_state_roundtrip(
        self, params: dict[str, Any], bars: list[BarInput]
    ) -> None:
        full = run_engine(create("market_structure", params), bars)
        cut = len(bars) // 2

        first = create("market_structure", params)
        head = run_engine(first, bars[:cut])
        second = create("market_structure", params)
        second.load_state(json.loads(json.dumps(first.dump_state())))

        assert head + run_engine(second, bars[cut:]) == full
