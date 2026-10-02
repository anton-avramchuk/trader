"""Trend: направление и сила тренда по структуре, EMA и эффективности."""

import json
from typing import Any

import pytest
from hypothesis import given, settings

from tests.indicators.helpers import bar_series, closes
from trader_engine.events import Event, create, current_events, known_at, run_engine
from trader_engine.indicators import BarInput

PARAMS: dict[str, Any] = {
    "threshold": "percent",
    "percent": 5,
    "ema_period": 3,
    "er_period": 4,
    "atr_period": 3,
}
UP = [100.0, 110, 105, 115, 110, 125, 118, 135, 128, 145, 138, 155, 148, 165]
DOWN = [165.0, 150, 155, 140, 145, 128, 135, 118, 125, 108, 115, 98, 105, 88]
CHOP = [100.0, 106, 100, 106, 100, 106, 100, 106, 100, 106, 100, 106]


def rows(events: list[Event]) -> list[tuple[str, str]]:
    return [(e.payload["state"], e.payload["bucket"]) for e in events]


def test_uptrend_with_pullbacks_is_a_trend() -> None:
    events = run_engine(create("trend", PARAMS), closes(UP))

    assert rows(events) == [("uptrend", "medium")]
    payload = events[0].payload
    assert payload["structure"] == "uptrend" and payload["ema_side"] == 1
    assert payload["since"] is not None


def test_reversal_goes_through_range_to_downtrend() -> None:
    events = run_engine(create("trend", PARAMS), closes(UP + DOWN))

    assert rows(events) == [
        ("uptrend", "medium"),
        ("uptrend", "strong"),
        ("range", "none"),
        ("downtrend", "medium"),
    ]
    assert events[1].payload["since"] == events[0].payload["since"]
    assert events[2].payload["since"] != events[1].payload["since"]


def test_choppy_market_without_direction_is_range_without_event_spam() -> None:
    events = run_engine(create("trend", PARAMS), closes(CHOP))

    assert rows(events) == [("range", "none")]


def test_current_picture_is_the_last_event_and_as_of_hides_later_ones() -> None:
    bars = closes(UP + DOWN)
    events = run_engine(create("trend", PARAMS), bars)

    known = known_at(events, bars[len(UP) - 1].close_time)

    assert rows(current_events(known)[-1:]) == [("uptrend", "medium")]
    assert known_at(events, bars[2].close_time) == []


def test_no_event_until_ema_and_structure_are_ready() -> None:
    assert run_engine(create("trend", PARAMS), closes(UP[:4])) == []


def test_params_are_validated() -> None:
    with pytest.raises(ValueError):
        create("trend", {"er_min": 2})


@pytest.mark.parametrize(
    "params", [{}, PARAMS, {"atr_period": 3, "atr_mult": 1.0, "ema_period": 4}]
)
class TestContract:
    @given(bars=bar_series(min_size=2, max_size=120))
    @settings(max_examples=40, deadline=None)
    def test_prefix_invariance(
        self, params: dict[str, Any], bars: list[BarInput]
    ) -> None:
        full = run_engine(create("trend", params), bars)
        prefix = run_engine(create("trend", params), bars[: max(len(bars) // 2, 1)])

        assert full[: len(prefix)] == prefix

    @given(bars=bar_series(min_size=4, max_size=120))
    @settings(max_examples=40, deadline=None)
    def test_state_roundtrip(
        self, params: dict[str, Any], bars: list[BarInput]
    ) -> None:
        full = run_engine(create("trend", params), bars)
        cut = len(bars) // 2
        first = create("trend", params)
        head = run_engine(first, bars[:cut])
        second = create("trend", params)
        second.load_state(json.loads(json.dumps(first.dump_state())))

        assert head + run_engine(second, bars[cut:]) == full
