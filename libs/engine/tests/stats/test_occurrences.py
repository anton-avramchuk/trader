"""Вхождения из лога событий: паттерны, касания и пробои уровней."""

from typing import Any

from tests.events.test_levels import CLOSES, SWING_ONLY, wick_bars
from tests.events.test_reversals import DOUBLE, PARAMS
from tests.indicators.helpers import closes
from trader_engine.events import Event, create, run_engine
from trader_engine.stats.occurrences import (
    GROUP_BREAK,
    GROUP_TOUCH,
    close_index,
    level_occurrences,
    occurrence_outcomes,
    pattern_occurrences,
)
from trader_engine.stats.outcomes import atr_series


def pattern_events(values: list[float], **params: Any) -> tuple[list[Event], list[Any]]:
    bars = closes(values)
    return run_engine(create("double_triple", PARAMS | params), bars), bars


class TestPatternOccurrences:
    def test_confirmed_pattern_enters_at_confirmation_with_target(self) -> None:
        events, _ = pattern_events([*DOUBLE, 99.0])

        [top] = [
            o
            for o in pattern_occurrences("double_triple", events)
            if o.group == "double_top"
        ]

        confirmed = next(e for e in events if e.status == "confirmed")
        assert top.entry == "confirmed"
        assert top.available_at == confirmed.available_at
        assert top.direction == "bearish"
        assert top.target == confirmed.payload["target"]
        assert top.invalidated_at is None and not top.false_breakout
        assert top.final_state == "confirmed"

    def test_unconfirmed_candidate_needs_the_flag(self) -> None:
        events, _ = pattern_events(DOUBLE)

        assert pattern_occurrences("double_triple", events) == []
        [candidate] = [
            o
            for o in pattern_occurrences(
                "double_triple", events, include_candidates=True
            )
            if o.group == "double_top"
        ]
        assert candidate.entry == "candidate"
        assert candidate.target is None and candidate.final_state == "candidate"
        assert candidate.available_at == events[0].available_at

    def test_cancelled_candidate_keeps_cancel_moment(self) -> None:
        events, _ = pattern_events([*DOUBLE, 112.0, 128.0, 130.0])

        occurrences = pattern_occurrences(
            "double_triple", events, include_candidates=True
        )

        cancelled = [o for o in occurrences if o.final_state == "invalidated"]
        assert cancelled
        for occurrence in cancelled:
            assert occurrence.entry == "candidate"
            assert occurrence.invalidated_at is not None
            assert occurrence.invalidated_at > occurrence.available_at
            assert occurrence.reason == "broken"

    def test_only_entries_visible_up_to_the_cut_are_returned(self) -> None:
        events, _ = pattern_events([*DOUBLE, 99.0])
        cut = next(e for e in events if e.status == "confirmed").available_at

        before = [e for e in events if e.available_at < cut]

        assert pattern_occurrences("double_triple", before) == []

    def test_false_breakout_is_flagged_and_enters_at_confirmation(self) -> None:
        events, _ = pattern_events([*DOUBLE, 99.0, 99.0, 125.0])

        occurrences = [
            o
            for o in pattern_occurrences("double_triple", events)
            if o.group == "double_top"
        ]

        for occurrence in occurrences:
            if occurrence.false_breakout:
                assert occurrence.entry == "confirmed"
                assert occurrence.reason == "false_breakout"
                assert occurrence.invalidated_at is not None


class TestLevelOccurrences:
    def events(self) -> list[Event]:
        return run_engine(create("levels", SWING_ONLY), wick_bars(CLOSES))

    def test_touches_and_breaks_with_direction_from_role(self) -> None:
        found = level_occurrences("levels", self.events())

        summary = [(o.group, o.direction, o.meta["level_id"]) for o in found]
        assert sorted(summary) == sorted(
            [
                (GROUP_TOUCH, "bearish", 1),  # касание сопротивления
                (GROUP_TOUCH, "bullish", 2),  # касание поддержки
                (GROUP_BREAK, "bullish", 1),  # пробой сопротивления вверх
                (GROUP_BREAK, "bullish", 3),
            ]
        )

    def test_entry_is_the_event_moment_and_strength_is_the_quality(self) -> None:
        events = self.events()
        touch = next(e for e in events if e.payload.get("change") == "touch")

        first = level_occurrences("levels", events)[0]

        assert first.available_at == touch.available_at
        assert first.entry == "touch"
        assert first.quality == touch.payload["strength"]["score"]
        assert first.target is None and first.invalidated_at is None

    def test_detected_and_expired_levels_are_not_occurrences(self) -> None:
        events = [e for e in self.events() if e.status != "revised"]

        assert level_occurrences("levels", events) == []


class TestOutcomesOfOccurrences:
    def test_every_level_occurrence_maps_to_a_bar(self) -> None:
        bars = wick_bars(CLOSES)
        events = run_engine(create("levels", SWING_ONLY), bars)
        atrs = atr_series(bars, 3)
        index = close_index(bars)

        for occurrence in level_occurrences("levels", events):
            outcomes = occurrence_outcomes(
                occurrence, bars, atrs, index, horizons=(2, 5)
            )
            assert outcomes is not None and len(outcomes) == 2

    def test_unknown_entry_bar_gives_none(self) -> None:
        bars = wick_bars(CLOSES)
        events = run_engine(create("levels", SWING_ONLY), bars)
        [first, *_] = level_occurrences("levels", events)

        assert (
            occurrence_outcomes(
                first, bars[:1], atr_series(bars[:1]), close_index(bars[:1])
            )
            is None
        )

    def test_pattern_target_hit_is_found_on_later_bars(self) -> None:
        events, bars = pattern_events([*DOUBLE, 99.0, 90.0, 80.0, 70.0])
        [top] = [
            o
            for o in pattern_occurrences("double_triple", events)
            if o.group == "double_top"
        ]

        outcomes = occurrence_outcomes(
            top, bars, atr_series(bars, 2), close_index(bars), horizons=(3,)
        )

        assert outcomes is not None
        assert outcomes[0].first_hit == "target"
        assert outcomes[0].ret_pct is not None and outcomes[0].ret_pct > 0
