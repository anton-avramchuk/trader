"""Head & Shoulders и Inverse H&S."""

import json
from typing import Any

import pytest
from hypothesis import given, settings

from tests.indicators.helpers import bar_series, closes
from trader_engine.events import Event, create, run_engine
from trader_engine.indicators import BarInput

PARAMS: dict[str, Any] = {"threshold": "percent", "percent": 10, "atr_period": 2}
# плечо 110, шея 98/99, голова 125, плечо 111: правое плечо подтверждается на баре 9
HS = [100.0, 110, 98, 109, 125, 100, 99, 110, 111, 99]


def run(values: list[float], **params: Any) -> list[Event]:
    return run_engine(create("head_shoulders", PARAMS | params), closes(values))


def patterns(events: list[Event], name: str = "") -> list[Event]:
    return [e for e in events if name in e.payload["pattern"]]


class TestHeadShoulders:
    def test_detected_then_confirmed_below_the_neckline(self) -> None:
        bars = closes([*HS, 97.0])

        events = patterns(run_engine(create("head_shoulders", PARAMS), bars), "head")

        assert [(e.payload["pattern"], e.payload["state"]) for e in events] == [
            ("head_shoulders", "candidate"),
            ("head_shoulders", "confirmed"),
        ]
        detected, confirmed = events
        assert detected.payload["direction"] == "bearish"
        assert [p["role"] for p in detected.payload["points"]] == [
            "left_shoulder",
            "neck1",
            "head",
            "neck2",
            "right_shoulder",
        ]
        assert detected.available_at == bars[9].close_time
        assert confirmed.confirmed_at == bars[10].close_time
        assert detected.payload["features"]["head_prominence_atr"] > 0.5

    def test_neckline_passes_through_both_troughs_and_sets_the_target(self) -> None:
        detected, confirmed = patterns(run([*HS, 97.0]), "head")
        line = detected.payload["line"]

        assert (line["p1"], line["p2"]) == (98, 99)
        assert line["i2"] - line["i1"] == 4
        height = 125 - (98 + (99 - 98) / 4 * 2)
        assert detected.payload["height"] == pytest.approx(height)
        level = confirmed.payload["breakout_level"]
        assert confirmed.payload["target"] == pytest.approx(level - height)

    def test_inverse_is_the_mirror_image(self) -> None:
        # процентный порог зависит от цены, поэтому у отражённого ряда он меньше
        mirrored = [225 - v for v in [*HS, 97.0]]
        events = patterns(run(mirrored, percent=7), "inverse")

        assert [e.payload["state"] for e in events] == ["candidate", "confirmed"]
        assert events[0].payload["direction"] == "bullish"
        assert events[0].payload["pattern"] == "inverse_head_shoulders"

    def test_head_must_stand_out(self) -> None:
        low_head = [100.0, 110, 98, 109, 112, 100, 99, 110, 111, 99]

        assert patterns(run(low_head), "head") == []

    def test_shoulders_must_be_roughly_equal(self) -> None:
        assert patterns(run(HS[:-2] + [135.0, 99.0]), "head") == []
        assert patterns(run(HS, shoulder_tol_atr=0.01), "head") == []

    def test_going_above_the_head_cancels_the_candidate(self) -> None:
        events = patterns(run([*HS, 112.0, 126.0, 130.0]), "head")

        assert [e.payload.get("reason") for e in events] == [None, "broken"]

    def test_quality_uses_precision_and_symmetry(self) -> None:
        detected = patterns(run(HS), "head")[0]

        parts = detected.payload["quality"]["components"]
        assert 0 < parts["precision"] <= 1 and 0 < parts["symmetry"] <= 1
        assert detected.payload["quality"]["version"] == 1


PARAM_SETS: list[dict[str, Any]] = [
    {"atr_period": 3, "atr_mult": 1.0, "shoulder_tol_atr": 2.0, "min_height_atr": 0.5},
    {"threshold": "percent", "percent": 2, "atr_period": 3, "head_min_atr": 0.1},
]


@pytest.mark.parametrize("params", PARAM_SETS)
class TestContract:
    @given(bars=bar_series(min_size=2, max_size=120))
    @settings(max_examples=40, deadline=None)
    def test_prefix_invariance(
        self, params: dict[str, Any], bars: list[BarInput]
    ) -> None:
        full = run_engine(create("head_shoulders", params), bars)
        cut = max(len(bars) // 2, 1)
        prefix = run_engine(create("head_shoulders", params), bars[:cut])

        assert full[: len(prefix)] == prefix

    @given(bars=bar_series(min_size=4, max_size=120))
    @settings(max_examples=40, deadline=None)
    def test_state_roundtrip(
        self, params: dict[str, Any], bars: list[BarInput]
    ) -> None:
        full = run_engine(create("head_shoulders", params), bars)
        cut = len(bars) // 2
        first = create("head_shoulders", params)
        head = run_engine(first, bars[:cut])
        second = create("head_shoulders", params)
        second.load_state(json.loads(json.dumps(first.dump_state())))

        assert head + run_engine(second, bars[cut:]) == full
