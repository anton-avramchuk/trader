"""Triangle, Wedge, Channel."""

import json
from collections.abc import Sequence
from typing import Any

import pytest
from hypothesis import given, settings

from tests.indicators.helpers import bar_series, closes
from trader_engine.events import Event, Line, create, run_engine
from trader_engine.events.trendlines import TrendlineParams, classify
from trader_engine.indicators import BarInput

PARAMS: dict[str, Any] = {"threshold": "percent", "percent": 5, "atr_period": 3}

SHAPES: dict[str, list[float]] = {
    "triangle_symmetric": [100, 120, 90, 113, 96, 108, 100, 106, 104],
    "triangle_ascending": [100, 120, 90, 120, 98, 120, 106, 120, 110, 112],
    "triangle_descending": [
        220 - x for x in [100, 120, 90, 120, 98, 120, 106, 120, 110, 112]
    ],
    "wedge_rising": [100, 110, 90, 118, 102, 124, 112, 128, 120, 126, 122],
    "wedge_falling": [
        230 - x for x in [100, 110, 90, 118, 102, 124, 112, 128, 120, 126, 122]
    ],
    "channel_ascending": [100, 110, 90, 120, 100, 130, 110, 140, 120, 130],
    "channel_descending": [
        250 - x for x in [100, 110, 90, 120, 100, 130, 110, 140, 120, 130]
    ],
    "channel_horizontal": [110, 120, 100, 120, 100, 120, 100, 120, 100, 110],
}
DIRECTIONS = {
    "triangle_symmetric": {"bullish", "bearish"},
    "triangle_ascending": {"bullish"},
    "triangle_descending": {"bearish"},
    "wedge_rising": {"bearish"},
    "wedge_falling": {"bullish"},
    "channel_ascending": {"bullish", "bearish"},
    "channel_descending": {"bullish", "bearish"},
    "channel_horizontal": {"bullish", "bearish"},
}


def path(pivots: Sequence[float], per: int = 4) -> list[float]:
    """Ломаная через опорные цены: по ``per`` баров на каждое колено."""
    out = [float(pivots[0])]
    for a, b in zip(pivots, pivots[1:], strict=False):
        out += [a + (b - a) * k / per for k in range(1, per + 1)]
    return out


def run(values: list[float], **params: Any) -> list[Event]:
    return run_engine(create("trendlines", PARAMS | params), closes(values))


@pytest.mark.parametrize("shape", list(SHAPES))
def test_shape_is_recognised_with_its_directions(shape: str) -> None:
    pivots = SHAPES[shape]
    events = run([*path(pivots), pivots[-1]])

    first = [e for e in events if e.payload["state"] == "candidate"]
    assert first and first[0].payload["pattern"] == shape
    opened = {e.payload["direction"] for e in first if e.payload["pattern"] == shape}
    assert opened == DIRECTIONS[shape]


class TestSymmetricTriangle:
    def test_breaking_one_side_cancels_the_other(self) -> None:
        events = run([*path(SHAPES["triangle_symmetric"]), 104.0])

        head = events[:4]
        assert [(e.payload["direction"], e.payload["state"]) for e in head] == [
            ("bullish", "candidate"),
            ("bearish", "candidate"),
            ("bullish", "confirmed"),
            ("bearish", "invalidated"),
        ]
        assert head[3].payload["reason"] == "broken"

    def test_payload_carries_touches_points_and_target(self) -> None:
        events = run([*path(SHAPES["triangle_symmetric"]), 104.0])
        detected, _, confirmed, _ = events[:4]

        payload = detected.payload
        assert [p["role"] for p in payload["points"]][:4] == [
            "high1",
            "low1",
            "high2",
            "low2",
        ]
        assert payload["features"]["touches_upper"] >= 2
        assert (
            payload["features"]["upper_slope_atr"]
            < 0
            < payload["features"]["lower_slope_atr"]
        )
        assert confirmed.payload["target"] == pytest.approx(
            confirmed.payload["breakout_level"] + payload["height"]
        )
        assert payload["line"]["t1"] and payload["line"]["t2"]


class TestDirectionalShapes:
    def test_ascending_triangle_is_cancelled_below_the_rising_support(self) -> None:
        events = run([*path(SHAPES["triangle_ascending"]), 100.0, 90.0])

        states = [(e.payload["state"], e.payload.get("reason")) for e in events]
        assert ("invalidated", "broken") in states
        assert {e.payload["direction"] for e in events} == {"bullish"}

    def test_rising_wedge_breaks_down(self) -> None:
        events = run([*path(SHAPES["wedge_rising"]), 100.0])

        wedge = [e for e in events if e.payload["pattern"] == "wedge_rising"]
        assert any(e.payload["state"] == "confirmed" for e in wedge)
        assert {e.payload["direction"] for e in wedge} == {"bearish"}


class TestRejections:
    def test_too_narrow_formation_is_ignored(self) -> None:
        assert (
            run([*path(SHAPES["triangle_symmetric"], per=1), 104.0], min_bars=10) == []
        )

    def test_before_atr_is_warm(self) -> None:
        assert run([*path(SHAPES["triangle_symmetric"])], atr_period=500) == []


class TestClassify:
    P = TrendlineParams()

    def test_crossing_lines_are_not_a_shape(self) -> None:
        upper, lower = Line(0, 100.0, 10, 100.0), Line(0, 95.0, 10, 105.0)

        assert classify(upper, lower, 0, 10, 2.0, self.P) is None

    def test_diverging_lines_are_not_a_shape(self) -> None:
        upper, lower = Line(0, 100.0, 10, 110.0), Line(0, 90.0, 10, 80.0)

        assert classify(upper, lower, 0, 10, 2.0, self.P) is None

    def test_parallel_and_converging(self) -> None:
        up, low = Line(0, 110.0, 10, 130.0), Line(0, 90.0, 10, 110.0)
        flat_low = Line(0, 90.0, 10, 100.0)
        flat_up = Line(0, 110.0, 10, 110.0)

        assert classify(up, low, 0, 10, 2.0, self.P) == "channel_ascending"
        assert classify(flat_up, flat_low, 0, 10, 2.0, self.P) == "triangle_ascending"


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
        full = run_engine(create("trendlines", params), bars)
        prefix = run_engine(
            create("trendlines", params), bars[: max(len(bars) // 2, 1)]
        )

        assert full[: len(prefix)] == prefix

    @given(bars=bar_series(min_size=4, max_size=120))
    @settings(max_examples=40, deadline=None)
    def test_state_roundtrip(
        self, params: dict[str, Any], bars: list[BarInput]
    ) -> None:
        full = run_engine(create("trendlines", params), bars)
        cut = len(bars) // 2
        first = create("trendlines", params)
        head = run_engine(first, bars[:cut])
        second = create("trendlines", params)
        second.load_state(json.loads(json.dumps(first.dump_state())))

        assert head + run_engine(second, bars[cut:]) == full
