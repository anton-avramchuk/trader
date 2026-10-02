"""Levels Engine: источники, касания, пробои, жизненный цикл, сила v1."""

import json
from typing import Any

import pytest
from hypothesis import given, settings

from tests.events.test_pivot import day_bars
from tests.indicators.helpers import bar_series, make_bars
from trader_engine.events import Event, create, current_events, known_at, run_engine
from trader_engine.events.levels import STRENGTH_V1, strength
from trader_engine.indicators import BarInput

type Rows = list[tuple[float, float, float, float, float]]

SWING_ONLY: dict[str, Any] = {
    "threshold": "percent",
    "percent": 10,
    "atr_period": 2,
    "pivot": False,
    "fibonacci": False,
    "cluster_k": 0,  # без слияния и простоя: проверяем «чистый» жизненный цикл
    "max_idle": 0,
}
CLOSES = (
    [100.0, 110, 120, 105]
    + [105] * 8
    + [112, 118, 119, 116, 108, 105, 105, 121, 124, 125, 125]
)


def wick_bars(values: list[float]) -> list[BarInput]:
    rows: Rows = [(c, c + 1, c - 1, c, 1.0) for c in values]
    return make_bars(rows)


def lifecycle(events: list[Event]) -> list[tuple[str, int, str, int]]:
    return [
        (e.status, e.payload["id"], e.payload["state"], e.payload["touches"])
        for e in events
    ]


class TestLifecycle:
    def test_swings_become_levels_then_touch_then_break(self) -> None:
        events = run_engine(create("levels", SWING_ONLY), wick_bars(CLOSES))

        assert lifecycle(events) == [
            ("detected", 1, "active", 0),  # swing high 121
            ("detected", 2, "active", 0),  # swing low 104
            ("revised", 1, "active", 1),  # касание сопротивления
            ("detected", 3, "active", 0),  # swing high 120
            ("revised", 2, "active", 1),  # касание поддержки
            ("detected", 4, "active", 0),  # swing low 104 (новый)
            ("revised", 1, "broken", 1),  # закрытие выше зоны
            ("revised", 3, "broken", 0),
        ]
        first, touch, broke = events[0], events[2], events[6]
        assert first.payload["role"] == "resistance" and first.payload["price"] == 121
        assert touch.payload["change"] == "touch" and touch.revises == first.seq
        assert broke.payload["role"] == "support"
        assert broke.payload["previous_role"] == "resistance"
        assert broke.revises == touch.seq

    def test_touch_raises_strength(self) -> None:
        events = run_engine(create("levels", SWING_ONLY), wick_bars(CLOSES))

        before, after = events[0].payload["strength"], events[2].payload["strength"]

        assert before["version"] == 1 and before["components"]["touches"] == 0
        assert after["components"]["touches"] == 1
        assert 0 <= before["score"] < after["score"] <= 100
        assert after["components"]["rejection"] > 0

    def test_level_is_not_touched_before_atr_is_warm(self) -> None:
        events = run_engine(
            create("levels", SWING_ONLY | {"atr_period": 500}), wick_bars(CLOSES)
        )

        assert events and all(e.status == "detected" for e in events)

    def test_old_level_expires(self) -> None:
        events = run_engine(
            create("levels", SWING_ONLY | {"max_age": 2}), wick_bars(CLOSES)
        )

        expired = [e for e in events if e.status == "invalidated"]
        assert expired and all(e.payload["reason"] == "expired" for e in expired)
        assert current_events(events) == [
            e for e in current_events(events) if e.status != "invalidated"
        ]

    def test_as_of_does_not_see_later_states(self) -> None:
        bars = wick_bars(CLOSES)
        events = run_engine(create("levels", SWING_ONLY), bars)

        broke = next(e for e in events if e.payload["state"] == "broken")
        known = current_events(known_at(events, broke.available_at.replace(minute=0)))

        assert all(e.payload["state"] == "active" for e in known)
        assert any(e.payload["state"] == "broken" for e in current_events(events))


class TestSources:
    def test_pivot_and_previous_levels_are_replaced_each_period(self) -> None:
        params = {"swing": False, "fibonacci": False, "atr_period": 2}

        events = run_engine(create("levels", params), day_bars())

        sources = {e.payload["source"] for e in events if e.status == "detected"}
        assert {
            "pivot_day_pp",
            "pivot_day_r3",
            "prev_day_high",
            "prev_day_close",
        } <= sources
        assert {"pivot_week_pp", "prev_week_low"} <= sources
        superseded = [e for e in events if e.status == "invalidated"]
        assert {e.payload["source"] for e in superseded} == {
            s for s in sources if s.startswith(("pivot_day", "prev_day"))
        }
        assert all(e.payload["reason"] == "superseded" for e in superseded)

    def test_fibonacci_levels_come_from_the_completed_leg(self) -> None:
        series = [100.0, 110, 120, 105, 100, 110, 130, 115, 105, 116, 100, 85, 96]
        params = {
            "threshold": "percent",
            "percent": 10,
            "swing": False,
            "pivot": False,
        }

        events = run_engine(create("levels", params), wick_bars(series))

        first = [e for e in events if e.payload["source"] == "fib_ret_38.2"]
        assert first[0].status == "detected"
        leg_start, leg_end = 121.0, 99.0  # первая нога: swing high 121 → swing low 99
        assert first[0].payload["price"] == pytest.approx(
            leg_end + 0.382 * (leg_start - leg_end)
        )
        assert any(e.status == "invalidated" for e in first)  # заменена новой ногой
        assert not any(e.payload["source"] == "fib_ext_100" for e in events)


def test_strength_formula_is_bounded_and_monotonic() -> None:
    level: dict[str, Any] = {
        "source_type": "swing",
        "touches": 0,
        "rebound_sum": 0.0,
        "age": 0,
    }
    weak = strength(level, 0)
    level |= {"touches": 10, "rebound_sum": 40.0, "age": 10_000}
    strong = strength(level, 10)

    assert weak["score"] == pytest.approx(100 * 0.25 * 0.6)
    assert weak["score"] < strong["score"] <= 100
    assert sum(STRENGTH_V1["weights"].values()) == pytest.approx(1.0)


PARAMS: list[dict[str, Any]] = [
    {},
    {"threshold": "percent", "percent": 2, "atr_period": 3},
    {"atr_period": 3, "atr_mult": 1.0, "touch_k": 0.5, "touch_m": 0.3, "max_age": 30},
]


@pytest.mark.parametrize("params", PARAMS)
class TestContract:
    @given(bars=bar_series(min_size=2, max_size=120))
    @settings(max_examples=40, deadline=None)
    def test_prefix_invariance(
        self, params: dict[str, Any], bars: list[BarInput]
    ) -> None:
        full = run_engine(create("levels", params), bars)
        prefix = run_engine(create("levels", params), bars[: max(len(bars) // 2, 1)])

        assert full[: len(prefix)] == prefix

    @given(bars=bar_series(min_size=4, max_size=120))
    @settings(max_examples=40, deadline=None)
    def test_state_roundtrip(
        self, params: dict[str, Any], bars: list[BarInput]
    ) -> None:
        full = run_engine(create("levels", params), bars)
        cut = len(bars) // 2
        first = create("levels", params)
        head = run_engine(first, bars[:cut])
        second = create("levels", params)
        second.load_state(json.loads(json.dumps(first.dump_state())))

        assert head + run_engine(second, bars[cut:]) == full

    @given(bars=bar_series(min_size=2, max_size=120))
    @settings(max_examples=40, deadline=None)
    def test_chains_are_consistent(
        self, params: dict[str, Any], bars: list[BarInput]
    ) -> None:
        events = run_engine(create("levels", params), bars)

        by_seq = {e.seq: e for e in events}
        for event in events:
            assert 0 <= event.payload["strength"]["score"] <= 100
            if event.revises is not None:
                assert by_seq[event.revises].payload["id"] == event.payload["id"]
            else:
                assert event.status == "detected"


class TestNoiseReduction:
    """ADR-0029: слияние близких swing-уровней и простой."""

    def test_a_swing_near_an_active_level_confirms_it_instead_of_adding_a_line(
        self,
    ) -> None:
        plain = run_engine(create("levels", SWING_ONLY), wick_bars(CLOSES))
        merged = run_engine(
            create("levels", SWING_ONLY | {"cluster_k": 0.5}), wick_bars(CLOSES)
        )

        def detected(events: list[Event]) -> int:
            return sum(1 for e in events if e.status == "detected")

        assert detected(merged) < detected(plain)
        confirmations = [e for e in merged if e.payload.get("change") == "merged"]
        assert confirmations
        assert all(
            e.status == "revised" and e.payload["state"] == "active"
            for e in confirmations
        )

    def test_a_swing_level_without_retouch_expires_by_idle(self) -> None:
        events = run_engine(
            create("levels", SWING_ONLY | {"max_idle": 5}), wick_bars(CLOSES)
        )

        idle = [e for e in events if e.payload.get("reason") == "idle"]
        assert idle
        assert all(
            e.status == "invalidated" and e.payload["state"] == "expired" for e in idle
        )

    def test_idle_is_off_by_zero_and_does_not_touch_other_families(self) -> None:
        events = run_engine(create("levels", SWING_ONLY), wick_bars(CLOSES))

        assert not [e for e in events if e.payload.get("reason") == "idle"]
