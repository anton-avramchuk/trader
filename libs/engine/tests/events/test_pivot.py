"""Pivot Engine: формулы, границы торговых дней и недель, каузальность."""

import json
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

import pytest
from hypothesis import given, settings

from tests.indicators.helpers import bar_series, make_bars
from trader_engine.events import create, run_engine
from trader_engine.events.pivot import levels
from trader_engine.indicators import BarInput

# (торговый день, high, low, close): по два бара в день
DAYS = [
    (date(2026, 9, 24), 110.0, 90.0, 100.0),  # чт
    (date(2026, 9, 25), 120.0, 100.0, 110.0),  # пт
    (date(2026, 9, 28), 130.0, 105.0, 125.0),  # пн (новая неделя)
]


def day_bars() -> list[BarInput]:
    rows: list[tuple[float, float, float, float, float]] = []
    for _, high, low, close in DAYS:
        rows += [(low, high, low, low, 1.0), (low, high, low, close, 1.0)]
    bars = make_bars(rows)
    return [
        replace(
            bar,
            trading_day=DAYS[i // 2][0],
            close_time=bar.timestamp + timedelta(minutes=15),
        )
        for i, bar in enumerate(bars)
    ]


def test_classic_levels_on_known_values() -> None:
    got = levels("classic", 120.0, 100.0, 110.0)

    assert got["pp"] == pytest.approx(110.0)
    assert got["r1"] == pytest.approx(120.0) and got["s1"] == pytest.approx(100.0)
    assert got["r2"] == pytest.approx(130.0) and got["s2"] == pytest.approx(90.0)
    assert got["r3"] == pytest.approx(140.0) and got["s3"] == pytest.approx(80.0)


@pytest.mark.parametrize("formula", ["classic", "fibonacci", "woodie", "camarilla"])
def test_levels_are_ordered(formula: str) -> None:
    got = levels(formula, 130.0, 100.0, 118.0)

    assert (
        got["s3"]
        < got["s2"]
        < got["s1"]
        < got["pp"]
        < got["r1"]
        < got["r2"]
        < got["r3"]
    )


def test_woodie_and_fibonacci_and_camarilla_values() -> None:
    assert levels("woodie", 120.0, 100.0, 110.0)["pp"] == pytest.approx(110.0)
    assert levels("fibonacci", 120.0, 100.0, 110.0)["r1"] == pytest.approx(117.64)
    assert levels("camarilla", 120.0, 100.0, 110.0)["r3"] == pytest.approx(115.5)


def test_daily_pivot_is_emitted_when_next_day_starts() -> None:
    bars = day_bars()

    events = run_engine(create("pivot", {"weekly": False}), bars)

    assert [e.payload["source_period"] for e in events] == ["2026-09-24", "2026-09-25"]
    first = events[0]
    assert first.payload["applies_to"] == "2026-09-25"
    assert (first.payload["high"], first.payload["low"], first.payload["close"]) == (
        110,
        90,
        100,
    )
    assert first.payload["pp"] == pytest.approx(100.0)
    assert first.available_at == bars[2].close_time  # первый бар нового дня


def test_weekly_pivot_only_on_week_change() -> None:
    events = run_engine(
        create("pivot", {"daily": False, "formula": "woodie"}), day_bars()
    )

    (weekly,) = events
    assert (
        weekly.payload["period"] == "week"
        and weekly.payload["source_period"] == "2026-W39"
    )
    assert weekly.payload["applies_to"] == "2026-09-28"
    assert (weekly.payload["high"], weekly.payload["low"], weekly.payload["close"]) == (
        120,
        90,
        110,
    )


def test_last_incomplete_period_gives_no_event() -> None:
    events = run_engine(create("pivot"), day_bars()[:5])

    assert all(e.payload["source_period"] != "2026-09-28" for e in events)


def test_invalid_formula_is_rejected() -> None:
    with pytest.raises(ValueError):
        create("pivot", {"formula": "magic"})


PARAMS: list[dict[str, Any]] = [{}, {"formula": "camarilla", "weekly": False}]


@pytest.mark.parametrize("params", PARAMS)
class TestContract:
    @given(bars=bar_series(min_size=2, max_size=120))
    @settings(max_examples=40, deadline=None)
    def test_prefix_invariance(
        self, params: dict[str, Any], bars: list[BarInput]
    ) -> None:
        full = run_engine(create("pivot", params), bars)
        prefix = run_engine(create("pivot", params), bars[: max(len(bars) // 2, 1)])

        assert full[: len(prefix)] == prefix

    @given(bars=bar_series(min_size=4, max_size=120))
    @settings(max_examples=40, deadline=None)
    def test_state_roundtrip(
        self, params: dict[str, Any], bars: list[BarInput]
    ) -> None:
        full = run_engine(create("pivot", params), bars)
        cut = len(bars) // 2
        first = create("pivot", params)
        head = run_engine(first, bars[:cut])
        second = create("pivot", params)
        second.load_state(json.loads(json.dumps(first.dump_state())))

        assert head + run_engine(second, bars[cut:]) == full
