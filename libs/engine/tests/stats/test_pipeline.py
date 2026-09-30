"""Сборка статистики: ряд, фильтры, группы, baseline, режим as-of."""

from dataclasses import replace
from datetime import timedelta

from tests.events.dataset import fixed_bars, perturb_future
from trader_engine.events import Event, create, run_engine
from trader_engine.indicators import BarInput
from trader_engine.stats.pipeline import (
    Filters,
    Series,
    SeriesOccurrence,
    build_series,
    collect,
    compute_statistics,
    matches,
    occurrence_detail,
)

BARS = fixed_bars()
HORIZONS = (3, 8)


def level_events(bars: list[BarInput]) -> list[Event]:
    return run_engine(create("levels"), bars)


def items_for(
    bars: list[BarInput] = BARS, key: str = "NG:15m", **series_kw: object
) -> list[SeriesOccurrence]:
    series = build_series(key, bars, **series_kw)  # type: ignore[arg-type]
    return collect(series, [("levels", level_events(bars))])


def test_series_has_aligned_arrays() -> None:
    series = build_series("NG:15m", BARS)

    assert len(series.atrs) == len(series.regimes) == len(series.bars) == len(BARS)
    assert series.index[BARS[5].close_time] == 5
    assert any(r is not None for r in series.regimes)


def test_as_of_cuts_bars_and_rolls() -> None:
    cut = BARS[99].close_time
    rolls = [BARS[10].close_time, BARS[200].close_time]

    series = build_series("NG:15m", BARS, roll_times=rolls, as_of=cut)

    assert len(series.bars) == 100
    assert list(series.roll_times) == [rolls[0]]


def test_collect_finds_level_touches_and_breaks() -> None:
    items = items_for()

    groups = {i.occurrence.group for i in items}
    assert groups <= {"level_touch", "level_break"} and groups
    assert all(i.entry_index is not None for i in items)


class TestFilters:
    def test_by_group_direction_and_period(self) -> None:
        items = items_for()
        touches = [i for i in items if i.occurrence.group == "level_touch"]
        assert touches
        first = touches[0].occurrence

        assert all(matches(i, Filters(groups=("level_touch",))) for i in touches)
        assert not any(
            matches(i, Filters(groups=("level_touch",)))
            for i in items
            if i.occurrence.group != "level_touch"
        )
        assert not matches(touches[0], Filters(direction=_other(first.direction)))
        assert matches(touches[0], Filters(direction=first.direction))
        assert not matches(
            touches[0], Filters(since=first.available_at + timedelta(seconds=1))
        )
        assert not matches(
            touches[0], Filters(until=first.available_at - timedelta(seconds=1))
        )

    def test_by_quality_range(self) -> None:
        item = items_for()[0]
        quality = item.occurrence.quality
        assert quality is not None

        assert matches(item, Filters(quality_min=quality, quality_max=quality))
        assert not matches(item, Filters(quality_min=quality + 1))
        assert not matches(item, Filters(quality_max=quality - 1))

    def test_by_regime_needs_a_known_regime(self) -> None:
        items = items_for()
        known = [i for i in items if i.regime is not None]
        unknown = [i for i in items if i.regime is None]
        assert known

        one = known[0]
        assert one.regime is not None
        assert matches(one, Filters(trends=(one.regime.trend,)))
        assert not matches(one, Filters(trends=("nonexistent",)))
        for item in unknown:
            assert not matches(item, Filters(trends=("uptrend", "downtrend", "range")))


def _other(direction: str) -> str:
    return "bearish" if direction == "bullish" else "bullish"


class TestStatistics:
    def test_groups_and_counts(self) -> None:
        items = items_for()

        result = compute_statistics(
            items, horizons=HORIZONS, group_by="direction", baseline=False
        )

        assert result.matched == len(items)
        assert sum(b.n_occurrences for b in result.buckets) == len(items)
        assert {b.key for b in result.buckets} <= {"bullish", "bearish"}
        for bucket in result.buckets:
            assert [h.horizon for h in bucket.horizons] == list(HORIZONS)

    def test_no_grouping_gives_one_bucket(self) -> None:
        result = compute_statistics(items_for(), horizons=HORIZONS)

        assert [b.key for b in result.buckets] == ["all"]

    def test_filters_reduce_the_sample(self) -> None:
        items = items_for()

        result = compute_statistics(
            items, filters=Filters(groups=("level_break",)), horizons=HORIZONS
        )

        assert result.matched == sum(i.occurrence.group == "level_break" for i in items)

    def test_deterministic_with_baseline(self) -> None:
        items = items_for()

        first = compute_statistics(items, horizons=HORIZONS, seed=3)
        again = compute_statistics(items, horizons=HORIZONS, seed=3)

        assert first == again

    def test_baseline_is_attached_when_regimes_are_known(self) -> None:
        items = [i for i in items_for() if i.regime is not None]

        result = compute_statistics(items, horizons=(3,), seed=1)

        stats = result.buckets[0].horizons[0]
        assert stats.baseline_n > 0
        assert stats.edge is not None

    def test_two_series_are_counted_separately(self) -> None:
        one = items_for(key="A")
        both = one + items_for(key="B")

        single = compute_statistics(one, horizons=(3,), baseline=False)
        double = compute_statistics(both, horizons=(3,), baseline=False)

        raw_single = single.buckets[0].horizons[0].n_raw
        raw_double = double.buckets[0].horizons[0].n_raw
        assert raw_double == 2 * raw_single

    def test_percent_unit(self) -> None:
        result = compute_statistics(
            items_for(), horizons=(3,), unit="pct", baseline=False
        )

        assert result.unit == "pct"
        assert result.buckets[0].horizons[0].unit == "pct"


class TestAsOf:
    def test_statistics_at_a_cut_do_not_depend_on_the_future(self) -> None:
        cut = 150
        at = BARS[cut].close_time
        changed = perturb_future(BARS, cut + 1)

        def stats(bars: list[BarInput]) -> object:
            series = build_series("NG:15m", bars, as_of=at)
            events = [e for e in level_events(bars) if e.available_at <= at]
            items = collect(series, [("levels", events)])
            return compute_statistics(items, horizons=HORIZONS, seed=2)

        assert stats(BARS) == stats(changed)

    def test_later_entries_become_censored_under_as_of(self) -> None:
        at = BARS[120].close_time
        full = items_for()
        cut_items = [i for i in full if i.occurrence.available_at <= at]

        late = build_series("NG:15m", BARS, as_of=at)
        rebuilt = [SeriesOccurrence(late, i.occurrence) for i in cut_items]
        result = compute_statistics(rebuilt, horizons=(50,), baseline=False)

        assert result.buckets[0].horizons[0].censored >= 1


class TestOccurrenceDetail:
    def test_outcomes_per_horizon_and_regime_label(self) -> None:
        item = next(i for i in items_for() if i.regime is not None)

        detail = occurrence_detail(item, HORIZONS)

        assert detail is not None
        assert [o.horizon for o in detail.outcomes] == list(HORIZONS)
        assert detail.regime == item.regime.label  # type: ignore[union-attr]
        assert detail.occurrence is item.occurrence

    def test_unknown_entry_bar_gives_none(self) -> None:
        item = items_for()[0]
        empty = Series("x", [], [], [], {})

        assert occurrence_detail(replace(item, series=empty)) is None
