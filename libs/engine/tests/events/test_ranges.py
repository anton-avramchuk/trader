"""Range breakout."""

import json
from typing import Any

import pytest
from hypothesis import given, settings

from tests.events.test_trendlines import path
from tests.indicators.helpers import bar_series, closes
from trader_engine.events import Event, create, run_engine
from trader_engine.indicators import BarInput

PARAMS: dict[str, Any] = {"threshold": "percent", "percent": 5, "atr_period": 3}
RANGE = [110, 120, 100, 120, 100, 120, 100, 120, 100, 110]


def run(values: list[float], **params: Any) -> list[Event]:
    return run_engine(create("range_breakout", PARAMS | params), closes(values))


def outcome(events: list[Event]) -> list[tuple[str, str, Any]]:
    return [
        (e.payload["direction"], e.payload["state"], e.payload.get("reason"))
        for e in events
    ]


def test_range_opens_two_occurrences() -> None:
    events = run(path(RANGE))

    assert outcome(events)[:2] == [
        ("bullish", "candidate", None),
        ("bearish", "candidate", None),
    ]
    payload = events[0].payload
    assert payload["pattern"] == "range_breakout"
    assert payload["features"]["range_high"] == pytest.approx(120)
    assert payload["features"]["range_low"] == pytest.approx(100)
    assert payload["features"]["touches_upper"] >= 2
    assert payload["height"] == pytest.approx(20)


def test_breakout_up_confirms_and_cancels_the_other_side() -> None:
    events = run([*path(RANGE), 118.0, 126.0])

    tail = outcome(events)[-2:]
    assert ("bullish", "confirmed", None) in tail
    assert ("bearish", "invalidated", "broken") in tail
    confirmed = next(e for e in events if e.payload["state"] == "confirmed")
    assert confirmed.payload["target"] == pytest.approx(
        confirmed.payload["breakout_level"] + 20
    )


def test_breakout_down_is_bearish() -> None:
    events = run([*path(RANGE), 104.0, 94.0])

    assert ("bearish", "confirmed", None) in outcome(events)
    assert ("bullish", "invalidated", "broken") in outcome(events)


def test_wick_inside_the_buffer_is_not_a_breakout() -> None:
    events = run([*path(RANGE), 121.0], break_atr=2.0)

    assert all(e.payload["state"] == "candidate" for e in events)


def test_trend_is_not_a_range() -> None:
    assert run(path([100, 110, 90, 120, 100, 130, 110, 140, 120, 130])) == []


def test_narrow_or_cold_ranges_are_ignored() -> None:
    assert run(path(RANGE), min_height_atr=10) == []
    assert run(path(RANGE), atr_period=500) == []
    assert run(path(RANGE, per=1), min_bars=10) == []


PARAM_SETS: list[dict[str, Any]] = [
    {"atr_period": 3, "atr_mult": 1.0, "tol_atr": 1.0, "min_height_atr": 0.5},
    {"threshold": "percent", "percent": 2, "atr_period": 3, "tol_atr": 2.0},
]


@pytest.mark.parametrize("params", PARAM_SETS)
class TestContract:
    @given(bars=bar_series(min_size=2, max_size=120))
    @settings(max_examples=40, deadline=None)
    def test_prefix_invariance(
        self, params: dict[str, Any], bars: list[BarInput]
    ) -> None:
        full = run_engine(create("range_breakout", params), bars)
        prefix = run_engine(
            create("range_breakout", params), bars[: max(len(bars) // 2, 1)]
        )

        assert full[: len(prefix)] == prefix

    @given(bars=bar_series(min_size=4, max_size=120))
    @settings(max_examples=40, deadline=None)
    def test_state_roundtrip(
        self, params: dict[str, Any], bars: list[BarInput]
    ) -> None:
        full = run_engine(create("range_breakout", params), bars)
        cut = len(bars) // 2
        first = create("range_breakout", params)
        head = run_engine(first, bars[:cut])
        second = create("range_breakout", params)
        second.load_state(json.loads(json.dumps(first.dump_state())))

        assert head + run_engine(second, bars[cut:]) == full
