"""Режим (тренд × волатильность) и baseline «случайная точка в том же режиме»."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tests.events.dataset import fixed_bars, perturb_future
from tests.stats.test_outcomes import bar
from trader_engine.events import create, run_engine
from trader_engine.indicators import BarInput
from trader_engine.stats.baseline import BaselineRequest, sample_baseline
from trader_engine.stats.outcomes import atr_series
from trader_engine.stats.regime import (
    Regime,
    regime_series,
    trend_series,
    volatility_series,
)


@dataclass
class FakeEvent:
    seq: int
    kind: str
    status: str
    available_at: datetime
    payload: dict[str, Any] = field(default_factory=dict[str, Any])


def trend_event(seq: int, at: datetime, state: str) -> FakeEvent:
    return FakeEvent(seq, "trend_state", "detected", at, {"state": state})


def flat(n: int) -> list[BarInput]:
    return [bar(i, 100.0) for i in range(n)]


class TestTrend:
    def test_state_applies_from_its_available_at(self) -> None:
        bars = flat(6)
        events = [
            trend_event(0, bars[1].close_time, "uptrend"),
            trend_event(1, bars[4].close_time, "downtrend"),
        ]

        assert trend_series(bars, events) == [
            None,
            "uptrend",
            "uptrend",
            "uptrend",
            "downtrend",
            "downtrend",
        ]

    def test_other_kinds_are_ignored_and_order_does_not_matter(self) -> None:
        bars = flat(3)
        events = [
            trend_event(1, bars[2].close_time, "range"),
            FakeEvent(0, "structure_point", "detected", bars[0].close_time),
        ]

        assert trend_series(bars, events) == [None, None, "range"]


class TestVolatility:
    def test_terciles_follow_history(self) -> None:
        bars = flat(9)
        atrs: list[float | None] = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]

        result = volatility_series(bars, atrs, min_history=3)

        # каждое новое значение — максимум истории → всегда high, кроме прогрева
        assert result[:2] == [None, None]
        assert set(result[2:]) == {"high"}

    def test_low_and_mid_levels(self) -> None:
        bars = flat(7)
        atrs: list[float | None] = [5.0, 6.0, 7.0, 8.0, 9.0, 5.5, 7.5]

        result = volatility_series(bars, atrs, min_history=3)

        assert result[5] == "low"  # 5.5 ниже почти всей истории
        assert result[6] == "mid"

    def test_missing_atr_gives_none(self) -> None:
        bars = flat(4)

        result = volatility_series(bars, [None, None, 1.0, 1.0], min_history=1)

        assert result[:2] == [None, None]
        assert result[2] is not None and result[3] is not None


class TestRegime:
    def test_needs_both_components(self) -> None:
        bars = flat(6)
        atrs: list[float | None] = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        events = [trend_event(0, bars[3].close_time, "uptrend")]

        regimes = regime_series(bars, atrs, events, min_history=2)

        assert regimes[:3] == [None, None, None]  # тренда ещё нет
        assert regimes[3] == Regime("uptrend", "high")
        assert regimes[3].label == "uptrend/high"  # type: ignore[union-attr]

    def test_regime_at_bar_does_not_depend_on_the_future(self) -> None:
        bars = fixed_bars()
        cut = len(bars) // 2
        changed = perturb_future(bars, cut)

        def regimes(data: list[BarInput]) -> list[Regime | None]:
            events = run_engine(create("market_structure"), data)
            return regime_series(data, atr_series(data), events)

        assert regimes(bars)[:cut] == regimes(changed)[:cut]


def make_regimes() -> list[Regime | None]:
    a, b = Regime("uptrend", "low"), Regime("downtrend", "high")
    return [None, None] + [a, b] * 30


class TestBaseline:
    def test_points_share_regime_and_direction_with_the_event(self) -> None:
        regimes = make_regimes()
        requests = [BaselineRequest(10, "bullish"), BaselineRequest(11, "bearish")]

        points = sample_baseline(regimes, requests, exclusion=2, per_event=4, seed=1)

        assert len(points) == 8
        for point in points:
            assert point.regime == regimes[point.index]
            assert point.regime == regimes[point.for_entry]
            assert point.direction == (
                "bullish" if point.for_entry == 10 else "bearish"
            )

    def test_points_stay_outside_event_windows(self) -> None:
        regimes = make_regimes()
        requests = [BaselineRequest(10, "bullish"), BaselineRequest(30, "bullish")]

        points = sample_baseline(regimes, requests, exclusion=5, per_event=50, seed=3)

        for point in points:
            assert all(abs(point.index - r.entry) > 5 for r in requests)

    def test_same_seed_same_sample_other_seed_other_sample(self) -> None:
        regimes = make_regimes()
        requests = [BaselineRequest(10, "bullish")]

        first = sample_baseline(regimes, requests, exclusion=2, seed=5)
        again = sample_baseline(regimes, requests, exclusion=2, seed=5)
        other = sample_baseline(regimes, requests, exclusion=2, seed=6)

        assert first == again
        assert first != other

    def test_unknown_regime_is_skipped_and_small_pool_is_capped(self) -> None:
        regimes: list[Regime | None] = [None, Regime("range", "mid")] + [None] * 6
        regimes += [Regime("range", "mid")] * 2

        points = sample_baseline(
            regimes,
            [BaselineRequest(0, "bullish"), BaselineRequest(1, "bullish")],
            exclusion=0,
            per_event=9,
        )

        # событие с неизвестным режимом пропущено; для второго пул — 1 бар
        assert [p.for_entry for p in points].count(0) == 0
        assert len([p for p in points if p.for_entry == 1]) == 2

    def test_a_bar_is_used_once_per_direction_and_the_pool_is_not_exceeded(
        self,
    ) -> None:
        regimes = make_regimes()
        requests = [BaselineRequest(10 + 2 * i, "bullish") for i in range(10)]

        points = sample_baseline(regimes, requests, exclusion=1, per_event=8, seed=4)

        pairs = [(p.index, p.direction) for p in points]
        assert len(pairs) == len(set(pairs))
        same_regime = {p.regime for p in points}
        assert len(same_regime) == 1
        pool = [
            i
            for i, r in enumerate(regimes)
            if r in same_regime and all(abs(i - q.entry) > 1 for q in requests)
        ]
        assert len(points) <= len(pool)

    def test_same_bar_may_serve_both_directions(self) -> None:
        regimes: list[Regime | None] = [Regime("range", "mid")] * 6

        points = sample_baseline(
            regimes,
            [BaselineRequest(0, "bullish"), BaselineRequest(0, "bearish")],
            exclusion=0,
            per_event=9,
            seed=1,
        )

        by_direction = {
            d: {p.index for p in points if p.direction == d}
            for d in ("bullish", "bearish")
        }
        assert by_direction["bullish"] == by_direction["bearish"] == {1, 2, 3, 4, 5}

    def test_order_of_requests_does_not_change_the_sample(self) -> None:
        regimes = make_regimes()
        a, b = BaselineRequest(10, "bullish"), BaselineRequest(11, "bearish")

        assert sample_baseline(regimes, [a, b], exclusion=2, seed=9) == (
            sample_baseline(regimes, [b, a], exclusion=2, seed=9)
        )
