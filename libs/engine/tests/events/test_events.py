"""Каркас движков событий: контракт, ревизии, состояние, as-of, отпечаток."""

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta

import pytest
from hypothesis import given, settings

from tests.events.toy import ToyHighs
from tests.indicators.helpers import bar_series, closes
from trader_engine.events import (
    Event,
    EventEngine,
    available,
    create,
    current_events,
    describe,
    fingerprint,
    hash_params,
    known_at,
    register,
    run_engine,
    unregister,
)
from trader_engine.indicators import BarInput

SERIES = [10.0, 11, 12, 11.5, 11.4, 11.3, 13, 12.9, 12.8, 12.7, 5, 6, 7, 20, 19, 18, 17]


@pytest.fixture
def toy() -> Iterator[None]:
    register(ToyHighs)
    yield
    unregister(ToyHighs.name)


@pytest.mark.usefixtures("toy")
class TestContract:
    def test_events_are_causal_and_ordered(self) -> None:
        bars = closes(SERIES)
        events = run_engine(create("toy_highs"), bars)

        close_times = {bar.close_time for bar in bars}
        assert [e.seq for e in events] == list(range(len(events)))
        assert all(e.available_at in close_times for e in events)
        assert all(e.detected_at <= e.available_at for e in events)
        assert [e.available_at for e in events] == sorted(
            e.available_at for e in events
        )
        for event in events:
            json.dumps(event.payload)

    def test_revisions_reference_earlier_events(self) -> None:
        events = run_engine(create("toy_highs"), closes(SERIES))

        assert {e.status for e in events} == {
            "detected",
            "revised",
            "confirmed",
            "invalidated",
        }
        for event in events:
            if event.status in ("revised", "invalidated"):
                assert event.revises is not None and event.revises < event.seq

    @given(bars=bar_series(min_size=2, max_size=60))
    @settings(max_examples=40, deadline=None)
    def test_prefix_invariance(self, bars: list[BarInput]) -> None:
        """События префикса совпадают с началом событий полного прогона."""
        register_needed = ToyHighs.name not in {c.name for c in available()}
        if register_needed:
            register(ToyHighs)
        try:
            cut = len(bars) // 2
            full = run_engine(create("toy_highs"), bars)
            prefix = run_engine(create("toy_highs"), bars[:cut])
        finally:
            if register_needed:
                unregister(ToyHighs.name)
        assert full[: len(prefix)] == prefix
        assert all(
            e.available_at > bars[cut - 1].close_time for e in full[len(prefix) :]
        )

    @given(bars=bar_series(min_size=2, max_size=60))
    @settings(max_examples=40, deadline=None)
    def test_state_roundtrip_continues_identically(self, bars: list[BarInput]) -> None:
        register_needed = ToyHighs.name not in {c.name for c in available()}
        if register_needed:
            register(ToyHighs)
        try:
            cut = len(bars) // 2
            full = run_engine(create("toy_highs"), bars)
            first = create("toy_highs")
            head = run_engine(first, bars[:cut])
            saved = json.loads(json.dumps(first.dump_state()))
            resumed = create("toy_highs")
            resumed.load_state(saved)
            tail = run_engine(resumed, bars[cut:])
        finally:
            if register_needed:
                unregister(ToyHighs.name)
        assert head + tail == full

    def test_bar_not_after_previous_is_rejected(self) -> None:
        bars = closes(SERIES)
        engine = create("toy_highs")
        engine.update(bars[3])

        with pytest.raises(ValueError, match="не позже"):
            engine.update(bars[3])
        with pytest.raises(ValueError, match="не позже"):
            engine.update(bars[1])


class Rogue(EventEngine):
    """Движок, нарушающий правила выпуска событий (для проверки защиты)."""

    name = "rogue"
    title = "Нарушитель"
    Params = ToyHighs.Params

    def __init__(self, mode: str = "ok") -> None:
        super().__init__()
        self.mode = mode

    def on_bar(self, bar: BarInput) -> None:
        if self.mode == "future":
            self.emit("x", "detected", {}, detected_at=bar.close_time + timedelta(1))
        elif self.mode == "unknown_revises":
            self.emit("x", "revised", {}, revises=5)
        elif self.mode == "revised_without_ref":
            self.emit("x", "revised", {})
        elif self.mode == "detected_with_ref":
            self.emit("x", "detected", {}, revises=0)
        elif self.mode == "payload":
            self.emit("x", "detected", {"when": bar.close_time})
        elif self.mode == "confirm_before_detect":
            self.emit(
                "x",
                "confirmed",
                {},
                detected_at=bar.close_time,
                confirmed_at=bar.timestamp,
            )

    def get_state(self) -> dict[str, object]:
        return {}

    def set_state(self, state: dict[str, object]) -> None:
        return None


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("future", "из будущего"),
        ("unknown_revises", "нет такого события"),
        ("revised_without_ref", "несовместимы"),
        ("detected_with_ref", "несовместимы"),
        ("payload", "JSON"),
        ("confirm_before_detect", "раньше detected_at"),
    ],
)
def test_engine_cannot_break_event_rules(mode: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Rogue(mode).update(closes([1.0])[0])


def test_confirmed_gets_confirmed_at_and_available_at_from_the_bar() -> None:
    class Once(Rogue):
        def on_bar(self, bar: BarInput) -> None:
            self.emit("x", "confirmed", {})

    bar = closes([1.0])[0]
    (event,) = Once().update(bar)

    assert event.confirmed_at == bar.close_time == event.available_at


def make(seq: int, status: str, revises: int | None, minutes: int) -> Event:
    bars = closes([1.0] * (minutes // 15 + 1))
    at = bars[-1].close_time
    return Event(
        seq=seq,
        kind="k",
        status=status,  # type: ignore[arg-type]
        payload={"s": seq},
        detected_at=at,
        confirmed_at=None,
        available_at=at,
        revises=revises,
    )


class TestResolve:
    EVENTS = [
        make(0, "detected", None, 0),
        make(1, "detected", None, 15),
        make(2, "revised", 0, 30),
        make(3, "invalidated", 1, 45),
        make(4, "confirmed", 2, 60),
    ]

    def test_current_keeps_latest_version_of_each_chain(self) -> None:
        current = current_events(self.EVENTS)

        assert [e.seq for e in current] == [4]  # цепочка 0→2→4; цепочка 1 отменена

    def test_as_of_shows_what_was_known_then(self) -> None:
        at_two = current_events(self.EVENTS, self.EVENTS[2].available_at)
        at_one = current_events(self.EVENTS, self.EVENTS[1].available_at)
        before = current_events(self.EVENTS, self.EVENTS[0].available_at - timedelta(1))

        assert [e.seq for e in at_two] == [2, 1]  # порядок — по началу цепочки
        assert [e.seq for e in at_one] == [0, 1]
        assert before == []

    def test_known_at_is_inclusive_of_available_at(self) -> None:
        edge = self.EVENTS[2].available_at

        assert [e.seq for e in known_at(self.EVENTS, edge)] == [0, 1, 2]

    def test_history_is_not_modified(self) -> None:
        snapshot = list(self.EVENTS)
        current_events(self.EVENTS)
        assert snapshot == self.EVENTS


class TestFingerprint:
    def test_same_bars_same_fingerprint_and_chain_continues(self) -> None:
        bars = closes(SERIES)

        assert fingerprint(bars) == fingerprint(list(bars))
        assert fingerprint(bars[5:], seed=fingerprint(bars[:5])) == fingerprint(bars)

    def test_any_change_or_reorder_changes_it(self) -> None:
        bars = closes(SERIES)
        edited = [*bars[:4], replace(bars[4], close=bars[4].close + 0.01), *bars[5:]]

        assert fingerprint(edited) != fingerprint(bars)
        assert fingerprint(bars[::-1]) != fingerprint(bars)
        assert fingerprint(bars[:-1]) != fingerprint(bars)


def test_registry_describes_and_hashes_params() -> None:
    register(ToyHighs)
    try:
        info = describe(ToyHighs)
        first = hash_params(ToyHighs.Params())
        second = hash_params(ToyHighs.Params(confirm_after=4))
    finally:
        unregister(ToyHighs.name)

    assert info["name"] == "toy_highs" and info["defaults"]["confirm_after"] == 3
    assert first != second and len(first) == 16


def test_registry_rejects_duplicates_unknown_names_and_bad_params() -> None:
    register(ToyHighs)
    try:
        with pytest.raises(ValueError, match="уже зарегистрирован"):
            register(ToyHighs)
        with pytest.raises(ValueError, match="confirm_after"):
            create("toy_highs", {"confirm_after": 0})
    finally:
        unregister(ToyHighs.name)
    with pytest.raises(KeyError, match="Неизвестный движок"):
        create("nope")
