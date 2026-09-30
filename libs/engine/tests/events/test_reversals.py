"""Double/Triple Top и Bottom."""

import json
from typing import Any

import pytest
from hypothesis import given, settings

from tests.indicators.helpers import bar_series, closes
from trader_engine.events import Event, create, run_engine
from trader_engine.indicators import BarInput

PARAMS: dict[str, Any] = {"threshold": "percent", "percent": 10, "atr_period": 2}
DOUBLE = [100.0, 110, 120, 110, 100, 110, 120, 107]
TRIPLE = [100.0, 110, 120, 110, 100, 110, 120, 110, 100, 110, 120, 107]


def run(values: list[float], **params: Any) -> list[Event]:
    return run_engine(create("double_triple", PARAMS | params), closes(values))


def summary(events: list[Event], only: str = "") -> list[tuple[str, str]]:
    """Паттерны событий; ``only`` оставляет паттерны, содержащие подстроку."""
    return [
        (e.payload["pattern"], e.payload["state"])
        for e in events
        if only in e.payload["pattern"]
    ]


def of(events: list[Event], only: str) -> list[Event]:
    return [e for e in events if only in e.payload["pattern"]]


class TestDouble:
    def test_double_top_candidate_then_confirmed_below_the_neckline(self) -> None:
        bars = closes([*DOUBLE, 99.0])

        events = run_engine(create("double_triple", PARAMS), bars)

        assert summary(events) == [
            ("double_top", "candidate"),
            ("double_top", "confirmed"),
        ]
        detected, confirmed = events
        assert detected.payload["direction"] == "bearish"
        assert [p["role"] for p in detected.payload["points"]] == [
            "top1",
            "valley",
            "top2",
        ]
        assert detected.available_at == bars[7].close_time
        assert confirmed.confirmed_at == bars[8].close_time
        assert confirmed.payload["target"] == pytest.approx(100 - 20)
        assert detected.payload["height"] == pytest.approx(20)

    def test_double_bottom_is_the_mirror_image(self) -> None:
        mirrored = [200 - v for v in [*DOUBLE, 99.0]]

        events = of(run(mirrored), "bottom")

        assert summary(events) == [
            ("double_bottom", "candidate"),
            ("double_bottom", "confirmed"),
        ]
        assert events[0].payload["direction"] == "bullish"
        assert [p["role"] for p in events[0].payload["points"]] == [
            "bottom1",
            "peak",
            "bottom2",
        ]
        assert events[1].payload["target"] == pytest.approx(100 + 20)

    def test_unequal_tops_are_not_a_double_top(self) -> None:
        assert run([100.0, 110, 120, 110, 100, 110, 135, 120]) == []

    def test_tolerance_is_a_parameter(self) -> None:
        values = [100.0, 110, 120, 110, 100, 110, 124, 110]

        assert run(values, tol_atr=0.1) == []
        assert summary(run(values, tol_atr=1.0))[:1] == [("double_top", "candidate")]

    def test_breaking_above_the_tops_cancels_the_candidate(self) -> None:
        events = run([*DOUBLE, 112.0, 128.0, 130.0])

        assert [e.payload.get("reason") for e in events] == [None, "broken"]

    def test_quality_reflects_how_equal_the_tops_are(self) -> None:
        exact = run(DOUBLE)[0].payload["quality"]["components"]["precision"]
        loose = run([100.0, 110, 120, 110, 100, 110, 122, 109], tol_atr=1.0)[0].payload[
            "quality"
        ]["components"]["precision"]

        assert exact == 1.0 and 0 <= loose < exact


class TestTriple:
    def test_triple_top_alongside_the_doubles_it_contains(self) -> None:
        events = of(run(TRIPLE), "top")

        assert summary(events) == [
            ("double_top", "candidate"),
            ("double_top", "candidate"),
            ("triple_top", "candidate"),
        ]
        triple = events[-1]
        assert [p["role"] for p in triple.payload["points"]] == [
            "top1",
            "valley1",
            "top2",
            "valley2",
            "top3",
        ]
        assert triple.payload["height"] == pytest.approx(20)

    def test_all_break_the_shared_neckline_together(self) -> None:
        events = run([*TRIPLE, 99.0])

        confirmed = [e for e in events if e.payload["state"] == "confirmed"]
        assert sorted(e.payload["pattern"] for e in confirmed) == [
            "double_top",
            "double_top",
            "triple_top",
        ]
        triple = next(e for e in confirmed if e.payload["pattern"] == "triple_top")
        assert triple.payload["target"] == pytest.approx(100 - 20)

    def test_triple_bottom_is_the_mirror_image(self) -> None:
        events = of(run([200 - v for v in TRIPLE]), "bottom")

        assert summary(events)[-1] == ("triple_bottom", "candidate")
        assert events[-1].payload["direction"] == "bullish"

    def test_third_top_too_high_leaves_only_the_double(self) -> None:
        values = [*TRIPLE[:-2], 135.0, 118.0]

        patterns = {e.payload["pattern"] for e in run(values)}

        assert "triple_top" not in patterns


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
        full = run_engine(create("double_triple", params), bars)
        prefix = run_engine(
            create("double_triple", params), bars[: max(len(bars) // 2, 1)]
        )

        assert full[: len(prefix)] == prefix

    @given(bars=bar_series(min_size=4, max_size=120))
    @settings(max_examples=40, deadline=None)
    def test_state_roundtrip(
        self, params: dict[str, Any], bars: list[BarInput]
    ) -> None:
        full = run_engine(create("double_triple", params), bars)
        cut = len(bars) // 2
        first = create("double_triple", params)
        head = run_engine(first, bars[:cut])
        second = create("double_triple", params)
        second.load_state(json.loads(json.dumps(first.dump_state())))

        assert head + run_engine(second, bars[cut:]) == full
