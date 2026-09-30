"""Swing points: ZigZag (ATR / процент) и fixed-window."""

import json
from typing import Any

import pytest
from hypothesis import given, settings

from tests.indicators.helpers import bar_series, closes, make_bars
from trader_engine.events import (
    Event,
    create,
    current_events,
    known_at,
    run_engine,
)
from trader_engine.indicators import BarInput

type Rows = list[tuple[float, float, float, float, float]]
PERCENT = {"threshold": "percent", "percent": 10}
SERIES = [100.0, 105, 110, 108, 99, 95, 100, 106, 112]


def brief(events: list[Event]) -> list[tuple[str, str, float]]:
    return [(e.status, e.payload["type"], e.payload["price"]) for e in events]


class TestZigZagPercent:
    def test_legs_confirmations_and_revisions(self) -> None:
        bars = closes(SERIES)

        events = run_engine(create("zigzag", PERCENT), bars)

        assert brief(events) == [
            ("detected", "high", 110),
            ("confirmed", "high", 110),
            ("detected", "low", 99),
            ("revised", "low", 95),
            ("confirmed", "low", 95),
            ("detected", "high", 106),
            ("revised", "high", 112),
        ]
        confirmed_high, confirmed_low = events[1], events[4]
        assert confirmed_high.confirmed_at == bars[4].close_time
        assert confirmed_high.available_at == bars[4].close_time
        assert confirmed_low.confirmed_at == bars[7].close_time
        assert confirmed_high.payload["timestamp"] == bars[2].timestamp.isoformat()
        assert events[3].revises == events[2].seq
        assert confirmed_low.revises == events[3].seq

    def test_current_picture_shows_latest_version_of_each_leg(self) -> None:
        events = run_engine(create("zigzag", PERCENT), closes(SERIES))

        current = current_events(events)

        assert brief(current) == [
            ("confirmed", "high", 110),
            ("confirmed", "low", 95),
            ("revised", "high", 112),
        ]

    def test_as_of_does_not_see_the_future(self) -> None:
        bars = closes(SERIES)
        events = run_engine(create("zigzag", PERCENT), bars)

        known = known_at(events, bars[4].close_time)

        assert brief(current_events(known)) == [
            ("confirmed", "high", 110),
            ("detected", "low", 99),
        ]

    def test_threshold_is_a_close_not_a_wick(self) -> None:
        rows: Rows = [
            (100, 100, 100, 100, 1),
            (100, 110, 100, 110, 1),
            (110, 111, 80, 105, 1),  # тень глубже порога, закрытие — нет
            (105, 106, 104, 104, 1),
        ]

        events = run_engine(create("zigzag", PERCENT), make_bars(rows))

        assert events == []

    def test_wide_bar_can_move_extreme_and_confirm_on_the_same_bar(self) -> None:
        rows: Rows = [(100, 100, 100, 100, 1), (100, 120, 90, 100, 1)]

        events = run_engine(create("zigzag", PERCENT), make_bars(rows))

        assert brief(events)[:2] == [
            ("detected", "high", 120),
            ("confirmed", "high", 120),
        ]
        assert brief(events)[2] == ("detected", "low", 90)


class TestZigZagAtr:
    def test_no_events_before_atr_is_warm(self) -> None:
        bars = closes([100.0, 50, 100, 50, 100])

        assert run_engine(create("zigzag", {"atr_period": 50}), bars) == []

    def test_threshold_scales_with_atr(self) -> None:
        # Колебания ±2 при ATR≈2 (порог 1.5×ATR = 3) — разворот; ±1 — шум.
        wave = [100.0, 102, 100, 98, 100, 102, 100, 98, 100, 102, 100, 98]
        noisy = [100.0 + (i % 2) for i in range(12)]
        rows: Rows = [(v, v + 1, v - 1, v, 1) for v in wave]

        real = run_engine(create("zigzag", {"atr_period": 3}), make_bars(rows))
        flat = run_engine(
            create("zigzag", {"atr_period": 3, "atr_mult": 3}),
            make_bars([(v, v + 0.5, v - 0.5, v, 1) for v in noisy]),
        )

        assert any(e.status == "confirmed" for e in real)
        assert all(e.payload["method"] == "zigzag_atr" for e in real)
        assert flat == []

    def test_confirmed_payload_carries_threshold(self) -> None:
        rows: Rows = [
            (v, v + 1, v - 1, v, 1) for v in [100.0, 102, 100, 98, 100, 102, 100, 98]
        ]
        events = run_engine(create("zigzag", {"atr_period": 3}), make_bars(rows))

        confirmed = [e for e in events if e.status == "confirmed"]
        assert confirmed and all(e.payload["threshold"] > 0 for e in confirmed)


class TestFixedWindow:
    def test_confirms_after_window_bars(self) -> None:
        values = [1.0, 2, 3, 9, 3, 2, 1, 0.5, 4, 5, 6]
        bars = closes(values)

        events = run_engine(create("swing_fixed", {"window": 2}), bars)

        assert brief(events) == [("confirmed", "high", 9), ("confirmed", "low", 0.5)]
        assert events[0].available_at == bars[5].close_time  # бар 3 + окно 2
        assert events[0].payload["timestamp"] == bars[3].timestamp.isoformat()
        assert events[1].payload["window"] == 2

    def test_plateau_takes_the_first_bar(self) -> None:
        events = run_engine(
            create("swing_fixed", {"window": 2}), closes([1.0, 2, 5, 5, 2, 1, 0.5])
        )

        highs = [e for e in events if e.payload["type"] == "high"]
        assert [e.payload["price"] for e in highs] == [5]
        assert (
            highs[0].payload["timestamp"]
            == closes([1.0, 2, 5])[2].timestamp.isoformat()
        )


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("zigzag", {}),
        ("zigzag", PERCENT),
        ("zigzag", {"atr_period": 3, "atr_mult": 1.0}),
        ("swing_fixed", {"window": 3}),
    ],
)
class TestContract:
    @given(bars=bar_series(min_size=2, max_size=90))
    @settings(max_examples=40, deadline=None)
    def test_prefix_invariance(
        self, name: str, params: dict[str, Any], bars: list[BarInput]
    ) -> None:
        full = run_engine(create(name, params), bars)

        for cut in (1, len(bars) // 2, len(bars) - 1):
            if cut < 1:
                continue
            prefix = run_engine(create(name, params), bars[:cut])
            assert full[: len(prefix)] == prefix
            assert all(
                e.available_at > bars[cut - 1].close_time for e in full[len(prefix) :]
            )

    @given(bars=bar_series(min_size=4, max_size=90))
    @settings(max_examples=40, deadline=None)
    def test_state_roundtrip_continues_identically(
        self, name: str, params: dict[str, Any], bars: list[BarInput]
    ) -> None:
        full = run_engine(create(name, params), bars)
        cut = len(bars) // 2

        first = create(name, params)
        head = run_engine(first, bars[:cut])
        state = json.loads(json.dumps(first.dump_state()))
        second = create(name, params)
        second.load_state(state)
        tail = run_engine(second, bars[cut:])

        assert head + tail == full

    @given(bars=bar_series(min_size=2, max_size=90))
    @settings(max_examples=40, deadline=None)
    def test_chains_are_well_formed(
        self, name: str, params: dict[str, Any], bars: list[BarInput]
    ) -> None:
        events = run_engine(create(name, params), bars)

        by_seq = {e.seq: e for e in events}
        for event in events:
            json.dumps(event.payload)
            assert event.kind == "swing"
            if event.revises is not None:
                parent = by_seq[event.revises]
                assert parent.payload["type"] == event.payload["type"]
                assert parent.available_at <= event.available_at
