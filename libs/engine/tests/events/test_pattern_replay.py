"""Replay паттернов: система «в прошлом» не знает будущего, цепочки согласованы."""

from datetime import datetime
from typing import Any

import pytest

from tests.events.dataset import fixed_bars
from tests.events.test_leakage import CONFIGS, IDS
from trader_engine.events import Event, create, current_events, known_at, run_engine

PATTERN_ENGINES = {"double_triple", "head_shoulders", "trendlines", "range_breakout"}
PATTERNS = [
    (key, name, params)
    for key, (name, params) in zip(IDS, CONFIGS, strict=True)
    if name in PATTERN_ENGINES
]
REASONS = {"broken", "expired", "false_breakout"}

pattern_cases = pytest.mark.parametrize(
    ("name", "params"),
    [(n, p) for _, n, p in PATTERNS],
    ids=[k for k, _, _ in PATTERNS],
)


def chains(events: list[Event]) -> dict[int, list[Event]]:
    """События по вхождениям (``payload.id``) в порядке появления."""
    result: dict[int, list[Event]] = {}
    for event in events:
        result.setdefault(event.payload["id"], []).append(event)
    return result


@pattern_cases
class TestOccurrences:
    def test_lifecycle_is_well_formed(self, name: str, params: dict[str, Any]) -> None:
        events = run_engine(create(name, params), fixed_bars())

        by_seq = {e.seq: e for e in events}
        for chain in chains(events).values():
            states = [e.payload["state"] for e in chain]
            assert states[0] == "candidate" and chain[0].revises is None
            assert states.count("candidate") == 1
            assert states.count("confirmed") <= 1 and states.count("invalidated") <= 1
            assert states[-1] != "candidate" or len(states) == 1
            if "invalidated" in states:
                assert states[-1] == "invalidated"
            for earlier, later in zip(chain, chain[1:], strict=False):
                assert later.revises == earlier.seq
                assert by_seq[later.seq].available_at >= earlier.available_at

    def test_confirmation_and_cancellation_carry_their_facts(
        self, name: str, params: dict[str, Any]
    ) -> None:
        events = run_engine(create(name, params), fixed_bars())

        for event in events:
            payload = event.payload
            assert 0 <= payload["quality"]["score"] <= 100
            assert payload["direction"] in {"bullish", "bearish"}
            if payload["state"] == "confirmed":
                assert payload["target"] is not None
                assert event.confirmed_at == event.available_at
            else:
                assert event.confirmed_at is None
            if payload["state"] == "invalidated":
                assert payload["reason"] in REASONS

    def test_points_are_known_before_the_event(
        self, name: str, params: dict[str, Any]
    ) -> None:
        events = run_engine(create(name, params), fixed_bars())

        for event in events:
            points = event.payload["points"]
            indexes = [p["index"] for p in points]
            assert indexes == sorted(indexes) and len(set(indexes)) == len(indexes)
            assert datetime.fromisoformat(event.payload["end"]) < event.available_at
            assert event.payload["start"] == points[0]["ts"]

    def test_as_of_picture_equals_a_run_up_to_that_moment(
        self, name: str, params: dict[str, Any]
    ) -> None:
        bars = fixed_bars()
        events = run_engine(create(name, params), bars)

        for cut in (60, 110, 170, 230):
            moment = bars[cut - 1].close_time
            replayed = run_engine(create(name, params), bars[:cut])

            assert current_events(known_at(events, moment)) == current_events(replayed)

    def test_no_occurrence_is_visible_before_its_confirming_swing(
        self, name: str, params: dict[str, Any]
    ) -> None:
        bars = fixed_bars()
        events = run_engine(create(name, params), bars)

        for chain in chains(events).values():
            last_point = chain[0].payload["points"][-1]["ts"]
            assert chain[0].available_at > datetime.fromisoformat(last_point)
