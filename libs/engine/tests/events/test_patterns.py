"""Каркас паттернов: геометрия, жизненный цикл, качество, состояние, каузальность."""

import json
from collections.abc import Iterator
from typing import Any

import pytest
from hypothesis import given, settings

from tests.events.toy_pattern import ToyDouble
from tests.indicators.helpers import bar_series, closes
from trader_engine.events import (
    Event,
    Line,
    create,
    current_events,
    fit_line,
    known_at,
    max_deviation,
    register,
    run_engine,
    unregister,
)
from trader_engine.events.patterns import QUALITY_V1, quality
from trader_engine.indicators import BarInput

PARAMS: dict[str, Any] = {"threshold": "percent", "percent": 10, "atr_period": 2}
# вершина 120, впадина 100, вершина 120: swing C подтверждается на баре 7 (закрытие 107)
BASE = [100.0, 110, 120, 110, 100, 110, 120, 107]


@pytest.fixture(autouse=True)
def toy() -> Iterator[None]:
    register(ToyDouble)
    yield
    unregister(ToyDouble.name)


def run(tail: list[float], **params: Any) -> tuple[list[Event], list[BarInput]]:
    bars = closes(BASE + tail)
    return run_engine(create("toy_double", PARAMS | params), bars), bars


def states(events: list[Event]) -> list[str]:
    return [e.payload["state"] for e in events]


class TestGeometry:
    def test_line_through_two_points(self) -> None:
        line = Line(2, 100.0, 6, 108.0)

        assert line.slope == 2.0
        assert line.at(4) == 104.0
        assert line.at(10) == 116.0  # продолжение за отрезком
        assert Line(0, 5.0, 3, 5.0).slope == 0.0
        assert Line(3, 5.0, 3, 9.0).slope == 0.0  # вырожденная
        assert Line.from_dict(line.to_dict()) == line

    def test_fit_recovers_exact_and_averages_noise(self) -> None:
        exact = fit_line([(0, 10.0), (5, 20.0), (10, 30.0)])
        noisy = fit_line([(0, 9.0), (1, 12.0), (2, 11.0), (3, 14.0)])

        assert (exact.slope, exact.at(7)) == pytest.approx((2.0, 24.0))
        assert (noisy.i1, noisy.i2) == (0, 3)
        assert 0.9 < noisy.slope < 1.6
        assert max_deviation(exact, [(0, 10.0), (5, 21.0)]) == pytest.approx(1.0)
        assert max_deviation(exact, []) == 0.0

    def test_fit_needs_two_distinct_indices(self) -> None:
        with pytest.raises(ValueError, match="двух точек"):
            fit_line([(1, 1.0)])
        with pytest.raises(ValueError, match="индексу"):
            fit_line([(1, 1.0), (1, 2.0)])


class TestLifecycle:
    def test_candidate_then_confirmed_by_close_below_the_neckline(self) -> None:
        events, bars = run([99.0, 98.0, 97.0, 96.0])

        assert states(events) == ["candidate", "confirmed"]
        detected, confirmed = events
        assert detected.available_at == bars[7].close_time
        assert confirmed.available_at == bars[8].close_time
        assert confirmed.confirmed_at == bars[8].close_time
        assert confirmed.revises == detected.seq
        assert confirmed.payload["target"] == pytest.approx(100 - 20)
        assert confirmed.payload["breakout_level"] == 100
        assert detected.payload["target"] is None
        assert detected.payload["direction"] == "bearish"

    def test_payload_describes_the_occurrence(self) -> None:
        (detected, *_), bars = run([105.0])

        payload = detected.payload
        assert payload["pattern"] == "double_top" and payload["height"] == 20
        assert [p["role"] for p in payload["points"]] == ["top1", "valley", "top2"]
        assert payload["start"] == bars[2].timestamp.isoformat()
        assert payload["end"] == bars[6].timestamp.isoformat()
        assert payload["line"]["p1"] == 100 and payload["line"]["i1"] == 4
        features = payload["features"]
        assert features["width_bars"] == 4 and features["height_atr"] > 1

    def test_close_above_the_pattern_high_breaks_the_candidate(self) -> None:
        events, _ = run([112.0, 121.0, 125.0])

        assert states(events) == ["candidate", "invalidated"]
        assert events[1].payload["reason"] == "broken"

    def test_candidate_expires(self) -> None:
        events, _ = run([105.0] * 6, max_bars=3)

        assert states(events) == ["candidate", "invalidated"]
        assert events[1].payload["reason"] == "expired"

    def test_false_breakout_inside_the_window_invalidates(self) -> None:
        events, _ = run([99.0, 101.0, 102.0])

        assert states(events) == ["candidate", "confirmed", "invalidated"]
        assert events[2].payload["reason"] == "false_breakout"
        assert events[2].revises == events[1].seq

    def test_breakout_that_holds_leaves_no_more_events(self) -> None:
        events, _ = run([99.0, 98.0, 97.0, 96.0, 105.0, 110.0])

        assert states(events) == ["candidate", "confirmed"]  # окно прошло

    def test_current_picture_and_as_of(self) -> None:
        events, bars = run([99.0, 98.0])

        assert states(current_events(events)) == ["confirmed"]
        known = known_at(events, bars[7].close_time)
        assert states(current_events(known)) == ["candidate"]

    def test_no_pattern_before_atr_is_warm_or_below_min_height(self) -> None:
        cold, _ = run([99.0], atr_period=500)
        flat, _ = run([99.0], min_height_atr=50)

        assert cold == [] and flat == []

    def test_unequal_tops_are_not_a_pattern(self) -> None:
        bars = closes([100.0, 110, 120, 110, 100, 110, 135, 120])

        assert run_engine(create("toy_double", PARAMS), bars) == []


class TestQuality:
    def test_aggregate_is_bounded_and_uses_documented_weights(self) -> None:
        best = quality({"precision": 1, "symmetry": 1}, 100.0, 1000)
        worst = quality({}, 0.0, 0)
        middle = quality({"precision": 0.5, "symmetry": 1.0}, 3.0, 15)

        assert best["score"] == 100 and worst["score"] == 0
        assert middle["components"] == {
            "precision": 0.5,
            "symmetry": 1.0,
            "height": 0.5,
            "duration": 0.5,
        }
        assert middle["score"] == pytest.approx(100 * (0.175 + 0.25 + 0.125 + 0.075))
        assert sum(QUALITY_V1["weights"].values()) == pytest.approx(1.0)

    def test_quality_is_attached_to_events(self) -> None:
        (detected, *_), _ = run([105.0])

        assert detected.payload["quality"]["version"] == 1
        assert 0 < detected.payload["quality"]["score"] <= 100


class TestContract:
    @given(bars=bar_series(min_size=2, max_size=120))
    @settings(max_examples=40, deadline=None)
    def test_prefix_invariance(self, bars: list[BarInput]) -> None:
        params = {
            "atr_period": 3,
            "atr_mult": 1.0,
            "tol_atr": 1.0,
            "min_height_atr": 0.5,
        }
        full = run_engine(create("toy_double", params), bars)
        prefix = run_engine(
            create("toy_double", params), bars[: max(len(bars) // 2, 1)]
        )

        assert full[: len(prefix)] == prefix

    @given(bars=bar_series(min_size=4, max_size=120))
    @settings(max_examples=40, deadline=None)
    def test_state_roundtrip(self, bars: list[BarInput]) -> None:
        params = {
            "atr_period": 3,
            "atr_mult": 1.0,
            "tol_atr": 1.0,
            "min_height_atr": 0.5,
        }
        full = run_engine(create("toy_double", params), bars)
        cut = len(bars) // 2
        first = create("toy_double", params)
        head = run_engine(first, bars[:cut])
        second = create("toy_double", params)
        second.load_state(json.loads(json.dumps(first.dump_state())))

        assert head + run_engine(second, bars[cut:]) == full

    def test_lookback_limits_remembered_bars(self) -> None:
        engine = create("toy_double", PARAMS | {"max_lookback": 10})
        run_engine(engine, closes(BASE * 5))

        assert len(engine.dump_state()["engine"]["times"]) <= 10
