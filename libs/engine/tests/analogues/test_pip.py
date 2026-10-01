"""PIP Engine и нормализация формы."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.stats.test_outcomes import bar
from trader_engine.analogues.pip import (
    normalize,
    pip_indices,
    window_shape,
)

ZIGZAG = [0.0, 5.0, 1.0, 6.0, 2.0, 7.0, 3.0]


def test_short_window_returns_all_points() -> None:
    assert pip_indices([1.0, 2.0, 3.0], k=5) == [0, 1, 2]


def test_endpoints_always_kept_and_extremes_found_first() -> None:
    values = [0.0, 1.0, 2.0, 10.0, 2.0, 1.0, 0.0, 0.5]
    assert pip_indices(values, k=3) == [0, 3, 7]


def test_straight_line_has_no_extra_points() -> None:
    assert pip_indices([float(i) for i in range(20)], k=6) == [0, 19]


def test_k_below_minimum_is_rejected() -> None:
    with pytest.raises(ValueError):
        pip_indices(ZIGZAG, k=2)


def test_ties_resolve_to_lowest_index() -> None:
    values = [0.0, 3.0, 0.0, 3.0, 0.0]
    assert pip_indices(values, k=3) == [0, 1, 4]


@given(
    st.lists(st.floats(-1e3, 1e3, allow_nan=False), min_size=2, max_size=60),
    st.integers(3, 12),
)
def test_indices_sorted_unique_and_bounded(values: list[float], k: int) -> None:
    result = pip_indices(values, k)
    assert result == sorted(set(result))
    assert len(result) <= k
    assert result[0] == 0
    assert result[-1] == len(values) - 1


def test_atr_normalization_starts_at_zero_and_scales_by_atr() -> None:
    prices = [100.0, 104.0, 102.0, 110.0]
    shape = normalize(prices, [0, 1, 3], "atr", atr=2.0)
    assert shape is not None
    assert shape.values == (0.0, 2.0, 5.0)
    assert shape.times == (0.0, 1 / 3, 1.0)


def test_percent_normalization() -> None:
    shape = normalize([100.0, 110.0, 90.0], [0, 1, 2], "percent", atr=None)
    assert shape is not None
    assert shape.values == pytest.approx((0.0, 10.0, -10.0))


def test_atr_normalization_needs_positive_atr() -> None:
    assert normalize([1.0, 2.0, 3.0], [0, 2], "atr", atr=None) is None
    assert normalize([1.0, 2.0, 3.0], [0, 2], "atr", atr=0.0) is None


def test_percent_normalization_rejects_zero_start() -> None:
    assert normalize([0.0, 1.0, 2.0], [0, 2], "percent", atr=None) is None


def test_atr_shape_is_invariant_to_price_shift_and_scale() -> None:
    base = [100.0, 104.0, 99.0, 108.0, 103.0]
    moved = [3.0 * p + 500.0 for p in base]
    a = normalize(base, [0, 1, 2, 3, 4], "atr", atr=2.0)
    b = normalize(moved, [0, 1, 2, 3, 4], "atr", atr=6.0)
    assert a is not None and b is not None
    assert b.values == pytest.approx(a.values)


def test_percent_shape_is_invariant_to_price_scale() -> None:
    base = [100.0, 104.0, 99.0, 108.0]
    a = normalize(base, [0, 1, 2, 3], "percent", atr=None)
    b = normalize([5.0 * p for p in base], [0, 1, 2, 3], "percent", atr=None)
    assert a is not None and b is not None
    assert b.values == pytest.approx(a.values)


def test_window_shape_uses_closes_and_k_points() -> None:
    bars = [bar(i, 100.0 + (i % 5) * 3) for i in range(30)]
    shape = window_shape(bars, atr=1.5, k=7)
    assert shape is not None
    assert len(shape.values) == 7
    assert shape.indices[0] == 0 and shape.indices[-1] == 29
    assert shape.times[0] == 0.0 and shape.times[-1] == 1.0


def test_window_shape_too_short_is_none() -> None:
    assert window_shape([bar(0, 1.0), bar(1, 2.0)], atr=1.0) is None
