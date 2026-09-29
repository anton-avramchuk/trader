from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trader_engine.intervals import (
    DateWindow,
    chunk_window,
    merge_windows,
    subtract_windows,
)


def d(day: int) -> date:
    return date(2026, 1, 1) + timedelta(days=day - 1)


def w(first: int, last: int) -> DateWindow:
    return (d(first), d(last))


class TestMerge:
    def test_overlapping_and_adjacent_windows_are_joined(self) -> None:
        assert merge_windows([w(10, 12), w(1, 3), w(4, 5), w(2, 4)]) == [
            w(1, 5),
            w(10, 12),
        ]

    def test_separate_windows_stay_separate(self) -> None:
        assert merge_windows([w(1, 2), w(4, 5)]) == [w(1, 2), w(4, 5)]

    def test_empty(self) -> None:
        assert merge_windows([]) == []

    def test_reversed_window_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="начало позже конца"):
            merge_windows([w(5, 1)])


class TestSubtract:
    def test_nothing_covered(self) -> None:
        assert subtract_windows(w(1, 10), []) == [w(1, 10)]

    def test_fully_covered(self) -> None:
        assert subtract_windows(w(3, 8), [w(1, 10)]) == []

    def test_covered_in_the_middle_leaves_both_ends(self) -> None:
        assert subtract_windows(w(1, 10), [w(4, 6)]) == [w(1, 3), w(7, 10)]

    def test_covered_prefix(self) -> None:
        assert subtract_windows(w(1, 10), [w(1, 4)]) == [w(5, 10)]

    def test_covered_suffix(self) -> None:
        assert subtract_windows(w(1, 10), [w(8, 20)]) == [w(1, 7)]

    def test_covered_outside_the_window_is_ignored(self) -> None:
        assert subtract_windows(w(5, 10), [w(1, 2), w(20, 30)]) == [w(5, 10)]

    def test_several_covered_windows(self) -> None:
        assert subtract_windows(w(1, 20), [w(3, 4), w(10, 12), w(11, 15)]) == [
            w(1, 2),
            w(5, 9),
            w(16, 20),
        ]

    def test_single_day_gap_between_covered_windows(self) -> None:
        assert subtract_windows(w(1, 9), [w(1, 4), w(6, 9)]) == [w(5, 5)]

    def test_extending_previous_coverage_loads_only_the_new_days(self) -> None:
        # Повторный запуск через день: загружено до 9-го, теперь доступно до 10-го.
        assert subtract_windows(w(1, 10), [w(1, 9)]) == [w(10, 10)]


class TestChunk:
    def test_exact_multiple(self) -> None:
        assert chunk_window(w(1, 6), 3) == [w(1, 3), w(4, 6)]

    def test_remainder_goes_to_the_last_chunk(self) -> None:
        assert chunk_window(w(1, 7), 3) == [w(1, 3), w(4, 6), w(7, 7)]

    def test_window_shorter_than_chunk(self) -> None:
        assert chunk_window(w(1, 2), 92) == [w(1, 2)]

    def test_single_day(self) -> None:
        assert chunk_window(w(5, 5), 10) == [w(5, 5)]

    def test_chunk_length_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="не меньше одного дня"):
            chunk_window(w(1, 5), 0)


days = st.integers(min_value=1, max_value=60)
windows = st.tuples(days, days).map(lambda pair: w(min(pair), max(pair)))


@given(target=windows, covered=st.lists(windows, max_size=6))
def test_subtract_leaves_exactly_the_uncovered_days(
    target: DateWindow, covered: list[DateWindow]
) -> None:
    gaps = subtract_windows(target, covered)

    def expand(items: list[DateWindow]) -> set[date]:
        return {
            a + timedelta(days=i) for a, b in items for i in range((b - a).days + 1)
        }

    assert expand(gaps) == expand([target]) - expand(covered)
    for (_, previous_end), (next_start, _) in zip(gaps, gaps[1:], strict=False):
        assert previous_end + timedelta(days=1) < next_start  # промежутки не слиплись


@given(target=windows, size=st.integers(min_value=1, max_value=20))
def test_chunks_tile_the_window_without_gaps_or_overlaps(
    target: DateWindow, size: int
) -> None:
    chunks = chunk_window(target, size)

    assert chunks[0][0] == target[0]
    assert chunks[-1][1] == target[1]
    for (_, previous_end), (next_start, _) in zip(chunks, chunks[1:], strict=False):
        assert previous_end + timedelta(days=1) == next_start
    assert all((b - a).days + 1 <= size for a, b in chunks)
