"""De-overlap, эффективный N, block bootstrap CI и агрегаты исходов."""

import pytest

from trader_engine.stats.aggregate import (
    Observation,
    bootstrap_ci,
    bootstrap_diff_ci,
    deoverlap,
    summarize,
)
from trader_engine.stats.outcomes import FirstHit, HorizonOutcome


def outcome(
    ret: float | None = 1.0,
    *,
    horizon: int = 5,
    censored: bool = False,
    first_hit: FirstHit | None = None,
    mfe: float = 2.0,
    mae: float = 1.0,
    ambiguous: bool = False,
    roll: bool = False,
) -> HorizonOutcome:
    if censored:
        return HorizonOutcome(
            horizon, True, None, None, None, None, None, None, None, False,
            False, False, False,
        )  # fmt: skip
    return HorizonOutcome(
        horizon=horizon,
        censored=False,
        ret_atr=ret,
        ret_pct=2.0 if ret is None else ret * 2,
        mfe_atr=None if ret is None else mfe,
        mfe_pct=mfe * 2,
        mae_atr=None if ret is None else mae,
        mae_pct=mae * 2,
        first_hit=first_hit,
        ambiguous_bar=ambiguous,
        crosses_session_gap=False,
        crosses_weekend=False,
        crosses_roll=roll,
    )


def obs(
    index: int, ret: float | None = 1.0, key: str = "A", **kw: object
) -> Observation:
    return Observation(key, index, outcome(ret, **kw))  # type: ignore[arg-type]


class TestDeoverlap:
    def test_keeps_first_and_drops_events_closer_than_the_window(self) -> None:
        items = [("A", 0), ("A", 3), ("A", 5), ("A", 9), ("A", 10)]

        kept = deoverlap(items, 5)

        assert [items[p][1] for p in kept] == [0, 5, 10]

    def test_series_are_independent(self) -> None:
        items = [("A", 0), ("B", 1), ("A", 2), ("B", 2)]

        kept = deoverlap(items, 5)

        assert [items[p] for p in kept] == [("A", 0), ("B", 1)]

    def test_unsorted_input_and_zero_window(self) -> None:
        items = [("A", 9), ("A", 0), ("A", 3)]

        assert [items[p][1] for p in deoverlap(items, 5)] == [9, 0]
        assert deoverlap(items, 0) == [0, 1, 2]

    def test_empty(self) -> None:
        assert deoverlap([], 5) == []


class TestBootstrap:
    VALUES = [0.5, 1.5, -0.2, 2.0, 0.9, 1.1, 0.3, 1.8, -0.4, 1.0, 0.7, 1.4]

    def test_deterministic_and_contains_the_mean(self) -> None:
        first = bootstrap_ci(self.VALUES, seed=3)
        again = bootstrap_ci(self.VALUES, seed=3)
        mean = sum(self.VALUES) / len(self.VALUES)

        assert first == again
        assert first is not None and first.low <= mean <= first.high

    def test_other_seed_gives_other_interval(self) -> None:
        assert bootstrap_ci(self.VALUES, seed=1) != bootstrap_ci(self.VALUES, seed=2)

    def test_constant_values_have_zero_width(self) -> None:
        ci = bootstrap_ci([0.4] * 20)

        assert ci is not None
        assert ci.low == pytest.approx(0.4) and ci.high == pytest.approx(0.4)

    def test_too_few_values(self) -> None:
        assert bootstrap_ci([]) is None
        assert bootstrap_ci([1.0]) is None

    def test_more_data_narrows_the_interval(self) -> None:
        small = bootstrap_ci(self.VALUES, seed=0)
        large = bootstrap_ci(self.VALUES * 20, seed=0)

        assert small is not None and large is not None
        assert large.high - large.low < small.high - small.low

    def test_wider_level_gives_wider_interval(self) -> None:
        narrow = bootstrap_ci(self.VALUES, level=0.5, seed=0)
        wide = bootstrap_ci(self.VALUES, level=0.95, seed=0)

        assert narrow is not None and wide is not None
        assert wide.high - wide.low > narrow.high - narrow.low

    def test_diff_ci_separates_shifted_samples_and_straddles_zero_for_equal(
        self,
    ) -> None:
        a = [v + 5 for v in self.VALUES]

        shifted = bootstrap_diff_ci(a, self.VALUES, seed=0)
        same = bootstrap_diff_ci(self.VALUES, self.VALUES, seed=0)

        assert shifted is not None and shifted.low > 4
        assert same is not None and same.low < 0 < same.high
        assert bootstrap_diff_ci([1.0], self.VALUES) is None


class TestSummarize:
    def test_counts_raw_effective_and_censored(self) -> None:
        events = [obs(0), obs(2), obs(5), obs(6, censored=True), obs(20, key="B")]

        stats = summarize(events, horizon=5, min_effective=1)

        # A: 0 и 5 (2 — ближе окна), B: 20; цензурированное не считается
        assert (stats.n_raw, stats.n_effective, stats.censored) == (4, 3, 1)

    def test_distribution_and_win_rate(self) -> None:
        events = [obs(0, 2.0), obs(10, -1.0), obs(20, 1.0), obs(30, 4.0)]

        stats = summarize(events, horizon=5, min_effective=1)

        assert stats.ret is not None
        assert stats.ret.mean == pytest.approx(1.5)
        assert stats.ret.median == pytest.approx(1.5)
        assert stats.win_rate == pytest.approx(0.75)
        assert stats.mfe is not None and stats.mfe.mean == 2.0
        assert stats.mae is not None and stats.mae.mean == 1.0
        assert stats.ret_ci is not None

    def test_unit_switch_uses_percent_values(self) -> None:
        events = [obs(0, 2.0), obs(10, 4.0)]

        stats = summarize(events, horizon=5, unit="pct", min_effective=1)

        assert stats.unit == "pct"
        assert stats.ret is not None and stats.ret.mean == pytest.approx(6.0)

    def test_small_sample_and_empty_warnings(self) -> None:
        few = summarize([obs(0), obs(10)], horizon=5)
        nothing = summarize([], horizon=5)
        only_censored = summarize([obs(0, censored=True)], horizon=5)

        assert "small_sample" in few.warnings
        assert nothing.warnings == ["no_data"] and nothing.ret is None
        assert only_censored.warnings == ["no_data"] and only_censored.censored == 1

    def test_missing_atr_is_dropped_from_atr_stats_but_counted(self) -> None:
        events = [obs(0, 1.0), obs(10, None), obs(20, 3.0)]

        stats = summarize(events, horizon=5, min_effective=1)

        assert stats.missing_atr == 1
        assert "missing_atr" in stats.warnings
        assert stats.ret is not None and stats.ret.mean == pytest.approx(2.0)
        assert (
            summarize(events, horizon=5, unit="pct", min_effective=1).missing_atr == 0
        )

    def test_target_and_invalidation_rates_and_flags(self) -> None:
        events = [
            obs(0, first_hit="target"),
            obs(10, first_hit="invalidated", ambiguous=True),
            obs(20, first_hit="target", roll=True),
            obs(30),
        ]

        stats = summarize(events, horizon=5, min_effective=1)

        assert stats.target_rate == pytest.approx(0.5)
        assert stats.invalidated_rate == pytest.approx(0.25)
        assert stats.flags["ambiguous_bar"] == 1
        assert stats.flags["crosses_roll"] == 1

    def test_edge_against_baseline(self) -> None:
        events = [obs(i * 10, 2.0) for i in range(20)]
        base = [obs(i * 10 + 3, 0.5, key="base") for i in range(40)]

        stats = summarize(events, base, horizon=5, min_effective=1)

        assert stats.baseline_n == 40
        assert stats.baseline_ret is not None and stats.baseline_ret.mean == 0.5
        assert stats.edge == pytest.approx(1.5)
        assert stats.edge_ci is not None and stats.edge_ci.low > 0
        assert "no_baseline" not in stats.warnings

    def test_tiny_baseline_gives_no_edge_but_a_warning(self) -> None:
        events = [obs(i * 10, 2.0) for i in range(20)]
        base = [obs(i * 10 + 3, -3.0, key="base") for i in range(4)]

        stats = summarize(events, base, horizon=5, min_effective=1)

        assert stats.baseline_n == 4
        assert stats.edge is None and stats.edge_ci is None
        assert "small_baseline" in stats.warnings
        assert "no_baseline" not in stats.warnings

    def test_baseline_threshold_is_configurable(self) -> None:
        events = [obs(i * 10, 2.0) for i in range(10)]
        base = [obs(i * 10 + 3, 0.5, key="base") for i in range(4)]

        stats = summarize(events, base, horizon=5, min_effective=1, min_baseline=3)

        assert stats.edge == pytest.approx(1.5)

    def test_missing_baseline_is_reported(self) -> None:
        stats = summarize([obs(0), obs(10)], horizon=5, min_effective=1)

        assert stats.edge is None and "no_baseline" in stats.warnings

    def test_baseline_censored_points_are_ignored(self) -> None:
        base = [obs(0, censored=True), obs(10, 1.0, key="b"), obs(20, 1.0, key="b")]

        stats = summarize([obs(0), obs(10)], base, horizon=5, min_effective=1)

        assert stats.baseline_n == 2

    def test_result_is_deterministic(self) -> None:
        events = [obs(i * 10, float(i % 5)) for i in range(40)]
        base = [obs(i * 10 + 3, float(i % 3), key="b") for i in range(40)]

        assert summarize(events, base, horizon=5, seed=4) == summarize(
            events, base, horizon=5, seed=4
        )
