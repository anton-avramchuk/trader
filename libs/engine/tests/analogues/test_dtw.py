"""DTW-ядро на известных векторах и свойства."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trader_engine.analogues.dtw import dtw, similarity
from trader_engine.analogues.pip import Shape


def shape(values: list[float], mode: str = "atr") -> Shape:
    n = len(values)
    times = tuple(i / (n - 1) for i in range(n))
    return Shape(tuple(range(n)), times, tuple(values), mode)  # type: ignore[arg-type]


def test_identical_shapes_have_zero_distance_and_unit_similarity() -> None:
    a = shape([0.0, 2.0, 1.0, 3.0])
    result = dtw(a, a)
    assert result is not None
    assert result.distance == 0.0
    assert result.similarity == 1.0
    assert result.path == ((0, 0), (1, 1), (2, 2), (3, 3))


def test_known_value_constant_offset() -> None:
    a = shape([0.0, 0.0, 0.0])
    b = shape([1.0, 1.0, 1.0])
    result = dtw(a, b)
    assert result is not None
    assert result.distance == pytest.approx(3.0)
    assert result.normalized_distance == pytest.approx(1.0)
    assert result.similarity == pytest.approx(0.5)


def test_time_warp_is_cheaper_than_pointwise_mismatch() -> None:
    a = shape([0.0, 1.0, 1.0, 1.0, 0.0])
    b = shape([0.0, 1.0, 0.0])
    warped = dtw(a, b, band=1.0, time_weight=0.0)
    assert warped is not None
    assert warped.distance == pytest.approx(0.0)
    assert len(warped.path) > 3


def test_band_zero_still_reaches_corner_for_unequal_lengths() -> None:
    result = dtw(shape([0.0, 1.0, 2.0, 3.0]), shape([0.0, 3.0]), band=0.0)
    assert result is not None
    assert result.path[0] == (0, 0) and result.path[-1] == (3, 1)


def test_different_normalizations_are_rejected() -> None:
    with pytest.raises(ValueError):
        dtw(shape([0.0, 1.0], "atr"), shape([0.0, 1.0], "percent"))


def test_invalid_band_is_rejected() -> None:
    with pytest.raises(ValueError):
        dtw(shape([0.0, 1.0]), shape([0.0, 1.0]), band=1.5)


def test_similarity_bounds() -> None:
    assert similarity(0.0) == 1.0
    assert 0.0 < similarity(1e6) < 1e-5


values = st.lists(st.floats(-50, 50, allow_nan=False), min_size=2, max_size=12)


@given(values, values)
def test_symmetric_and_non_negative(x: list[float], y: list[float]) -> None:
    ab = dtw(shape(x), shape(y))
    ba = dtw(shape(y), shape(x))
    assert ab is not None and ba is not None
    assert ab.distance >= 0.0
    assert ab.distance == pytest.approx(ba.distance)
    assert 0.0 < ab.similarity <= 1.0


@given(values, values, st.floats(-100, 100, allow_nan=False))
def test_common_shift_does_not_change_distance(
    x: list[float], y: list[float], delta: float
) -> None:
    base = dtw(shape(x), shape(y))
    shifted = dtw(shape([v + delta for v in x]), shape([v + delta for v in y]))
    assert base is not None and shifted is not None
    assert shifted.distance == pytest.approx(base.distance, abs=1e-6)


@given(values, values)
def test_path_is_monotone_and_spans_corners(x: list[float], y: list[float]) -> None:
    result = dtw(shape(x), shape(y))
    assert result is not None
    assert result.path[0] == (0, 0)
    assert result.path[-1] == (len(x) - 1, len(y) - 1)
    for (i0, j0), (i1, j1) in zip(result.path, result.path[1:], strict=False):
        assert 0 <= i1 - i0 <= 1 and 0 <= j1 - j0 <= 1
        assert (i1, j1) != (i0, j0)
