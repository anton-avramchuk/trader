"""Ядро Forecast: вероятности порогов, квантили, прореживание, малая выборка."""

from collections.abc import Sequence

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trader_engine.forecast.core import forecast_horizon
from trader_engine.stats.aggregate import Observation
from trader_engine.stats.outcomes import HorizonOutcome


def outcome(
    ret: float | None, *, mfe: float = 1.0, mae: float = 0.5, censored: bool = False
) -> HorizonOutcome:
    return HorizonOutcome(
        horizon=5,
        censored=censored,
        ret_atr=None if censored else ret,
        ret_pct=None if censored else (None if ret is None else ret / 2),
        mfe_atr=None if censored else mfe,
        mfe_pct=None if censored else mfe / 2,
        mae_atr=None if censored else mae,
        mae_pct=None if censored else mae / 2,
        first_hit=None,
        ambiguous_bar=False,
        crosses_session_gap=False,
        crosses_weekend=False,
        crosses_roll=False,
    )


def observations(returns: Sequence[float | None], step: int = 10) -> list[Observation]:
    return [
        Observation(("S", "bullish"), i * step, outcome(r))
        for i, r in enumerate(returns)
    ]


def test_threshold_probabilities_and_quantiles() -> None:
    returns = [-3.0, -1.2, -0.6, -0.2, 0.1, 0.3, 0.7, 1.1, 1.5, 2.5]
    result = forecast_horizon(observations(returns), horizon=5, min_effective=5)

    assert result.n_raw == result.n_effective == 10
    by = {p.threshold: p for p in result.probabilities}
    assert by[0.5].up == pytest.approx(0.4)  # 0.7, 1.1, 1.5, 2.5
    assert by[0.5].down == pytest.approx(0.3)  # -3.0, -1.2, -0.6
    assert by[1.0].up == pytest.approx(0.3)
    assert by[1.0].down == pytest.approx(0.2)
    assert by[2.0].up == pytest.approx(0.1)
    assert by[2.0].down == pytest.approx(0.1)
    assert result.median_ret == pytest.approx(0.2)
    assert result.quantiles[10] < result.quantiles[25] < result.quantiles[75]
    assert result.quantiles[75] < result.quantiles[90]
    assert result.median_mfe == 1.0 and result.median_mae == 0.5
    assert result.warnings == []
    assert result.ret_ci is not None and result.ret_ci.low <= result.mean_ret  # type: ignore[operator]


def test_small_sample_and_empty_warnings() -> None:
    small = forecast_horizon(observations([0.1, 0.2, 0.3]), horizon=5)
    assert "small_sample" in small.warnings and small.probabilities

    empty = forecast_horizon([], horizon=5)
    assert empty.warnings == ["no_data"]
    assert empty.probabilities == [] and empty.median_ret is None


def test_censored_are_excluded_and_counted() -> None:
    items = observations([0.5, 0.6]) + [
        Observation(("S", "bullish"), 500, outcome(None, censored=True))
    ]

    result = forecast_horizon(items, horizon=5, min_effective=1)

    assert result.n_raw == 2 and result.censored == 1


def test_overlapping_events_are_thinned_by_horizon() -> None:
    close = observations([1.0, 1.0, 1.0, 1.0], step=2)  # горизонт 5 перекрывает

    result = forecast_horizon(close, horizon=5, min_effective=1)

    assert result.n_raw == 4 and result.n_effective == 2


def test_missing_atr_is_flagged() -> None:
    items = observations([0.5, 0.6, None])

    result = forecast_horizon(items, horizon=5, min_effective=1)

    assert "missing_atr" in result.warnings and result.missing_atr == 1
    assert result.n_effective == 3


def test_pct_unit_uses_percent_fields() -> None:
    result = forecast_horizon(
        observations([2.0, 4.0]), horizon=5, unit="pct", min_effective=1
    )

    assert result.unit == "pct" and result.median_ret == pytest.approx(1.5)


def test_custom_thresholds_and_determinism() -> None:
    items = observations([0.2, -0.2, 0.9, 1.4, -1.6, 0.0])
    a = forecast_horizon(items, horizon=5, thresholds=(0.25,), seed=3)
    b = forecast_horizon(items, horizon=5, thresholds=(0.25,), seed=3)

    assert a == b and [p.threshold for p in a.probabilities] == [0.25]


@given(st.lists(st.floats(-20, 20, allow_nan=False), min_size=2, max_size=60))
def test_probability_invariants(returns: list[float]) -> None:
    result = forecast_horizon(observations(list(returns)), horizon=5, min_effective=1)

    last_up, last_down = 1.0, 1.0
    for p in result.probabilities:
        assert 0.0 <= p.up <= 1.0 and 0.0 <= p.down <= 1.0
        assert p.up + p.down <= 1.0 + 1e-9  # пороги > 0: события не пересекаются
        assert p.up <= last_up and p.down <= last_down  # монотонны по порогу
        last_up, last_down = p.up, p.down
    q = result.quantiles
    assert q[10] <= q[25] <= q[75] <= q[90]
    assert min(returns) <= result.median_ret <= max(returns)  # type: ignore[operator]
