"""Шаблон стратегии: сигналы, фильтры, стоп и цель, валидация, сериализация."""

from datetime import timedelta
from typing import Any

import pytest

from tests.stats.test_outcomes import START, bar
from trader_engine.backtest.strategy import (
    ExitRule,
    StrategySpec,
    build_signals,
)
from trader_engine.stats.occurrences import Occurrence, close_index
from trader_engine.stats.pipeline import Series, SeriesOccurrence
from trader_engine.stats.regime import Regime

N = 60


def make_series(
    atr: float | None = 2.0, regimes: list[Regime | None] | None = None
) -> Series:
    bars = [bar(i, 100.0 + i) for i in range(N)]
    return Series("S", bars, [atr] * N, regimes or [None] * N, close_index(bars))


def occ(
    series: Series,
    index: int,
    *,
    kind: str = "pattern",
    group: str = "double_bottom",
    direction: str = "bullish",
    entry: str = "confirmed",
    quality: float | None = 70.0,
    target: float | None = None,
    extreme: float | None = None,
) -> SeriesOccurrence:
    meta: dict[str, Any] = {} if extreme is None else {"extreme": extreme}
    return SeriesOccurrence(
        series,
        Occurrence(
            key=f"e:{kind}:{group}:{index}",
            engine="e",
            kind=kind,  # type: ignore[arg-type]
            group=group,
            direction=direction,  # type: ignore[arg-type]
            entry=entry,  # type: ignore[arg-type]
            available_at=series.bars[index].close_time,
            target=target,
            quality=quality,
            meta=meta,
        ),
    )


def test_atr_stop_and_target_from_signal_close() -> None:
    series = make_series()
    built = build_signals(StrategySpec(), [occ(series, 20)])

    [signal] = built.signals
    ref = series.bars[20].close
    assert (signal.bar_index, signal.side, signal.ref) == (
        20,
        "long",
        "e:pattern:double_bottom:20",
    )
    assert signal.stop == pytest.approx(ref - 1.5 * 2.0)
    assert signal.target == pytest.approx(ref + 3.0 * 2.0)


def test_bearish_follow_and_fade_sides() -> None:
    series = make_series()
    event = occ(series, 20, direction="bearish")

    [follow] = build_signals(StrategySpec(), [event]).signals
    [fade] = build_signals(StrategySpec(side="fade"), [event]).signals

    ref = series.bars[20].close
    assert follow.side == "short" and follow.stop == pytest.approx(ref + 3.0)
    assert fade.side == "long" and fade.stop == pytest.approx(ref - 3.0)
    assert fade.target == pytest.approx(ref + 6.0)


def test_structure_stop_and_pattern_target() -> None:
    series = make_series()
    ref = series.bars[20].close
    event = occ(series, 20, extreme=ref - 5, target=ref + 12)
    spec = StrategySpec(stop=ExitRule("structure"), target=ExitRule("pattern"))

    [signal] = build_signals(spec, [event]).signals

    assert signal.stop == ref - 5 and signal.target == ref + 12


def test_levels_beyond_price_are_rejected() -> None:
    series = make_series()
    ref = series.bars[20].close
    spec = StrategySpec(stop=ExitRule("structure"), target=ExitRule("pattern"))
    wrong_stop = occ(series, 20, extreme=ref + 1, target=ref + 12)  # стоп выше цены
    wrong_target = occ(series, 25, extreme=series.bars[25].close - 5, target=1.0)
    no_levels = occ(series, 30)

    built = build_signals(spec, [wrong_stop, wrong_target, no_levels])

    assert built.signals == [] and built.bad_levels == 3


def test_missing_atr_skips_atr_rules_only() -> None:
    series = make_series(atr=None)
    event = occ(series, 20, extreme=series.bars[20].close - 5)

    atr = build_signals(StrategySpec(), [event])
    structure = build_signals(
        StrategySpec(stop=ExitRule("structure"), target=ExitRule("none"), max_bars=5),
        [event],
    )

    assert atr.signals == [] and atr.no_atr == 1
    assert len(structure.signals) == 1


def test_source_and_group_filters() -> None:
    series = make_series()
    pattern = occ(series, 10)
    other = occ(series, 12, group="double_top")
    candidate = occ(series, 14, entry="candidate")
    touch = occ(series, 16, kind="level", group="level_touch", entry="touch")
    brk = occ(series, 18, kind="level", group="level_break", entry="break")
    items = [pattern, other, candidate, touch, brk]

    def picked(spec: StrategySpec) -> list[int]:
        return [s.bar_index for s in build_signals(spec, items).signals]

    assert picked(StrategySpec()) == [10, 12]
    assert picked(StrategySpec(groups=("double_top",))) == [12]
    assert picked(StrategySpec(source="level_touch")) == [16]
    assert picked(StrategySpec(source="level_break")) == [18]


def test_quality_regime_and_period_filters() -> None:
    up, down = Regime("uptrend", "mid"), Regime("downtrend", "high")
    series = make_series(regimes=[up if i < 30 else down for i in range(N)])
    items = [
        occ(series, 10, quality=40),
        occ(series, 20, quality=80),
        occ(series, 40, quality=90),
    ]

    def picked(**kw: Any) -> list[int]:
        return [s.bar_index for s in build_signals(StrategySpec(**kw), items).signals]

    assert picked(quality_min=60) == [20, 40]
    assert picked(quality_max=60) == [10]
    assert picked(trends=("uptrend",)) == [10, 20]
    assert picked(volatilities=("high",)) == [40]
    assert picked(since=series.bars[20].close_time) == [20, 40]
    assert picked(until=series.bars[20].close_time - timedelta(seconds=1)) == [10]


def test_signals_are_sorted_by_time_and_use_only_signal_bar_data() -> None:
    series = make_series()
    items = [occ(series, 30), occ(series, 10), occ(series, 20)]

    signals = build_signals(StrategySpec(), items).signals

    assert [s.bar_index for s in signals] == [10, 20, 30]
    assert series.bars[10].close_time > START  # время сигнала = закрытие бара


@pytest.mark.parametrize(
    "spec",
    [
        StrategySpec(source="nonsense"),  # type: ignore[arg-type]
        StrategySpec(stop=ExitRule("atr", 0)),
        StrategySpec(stop=ExitRule("weird")),
        StrategySpec(target=ExitRule("weird")),
        StrategySpec(source="level_touch", stop=ExitRule("structure")),
        StrategySpec(source="level_break", target=ExitRule("pattern")),
        StrategySpec(side="fade", stop=ExitRule("structure")),
        StrategySpec(max_bars=0),
        StrategySpec(stop=ExitRule("none"), target=ExitRule("none")),
    ],
)
def test_invalid_specs_are_rejected(spec: StrategySpec) -> None:
    with pytest.raises(ValueError):
        build_signals(spec, [])


def test_time_only_strategy_is_valid() -> None:
    spec = StrategySpec(stop=ExitRule("none"), target=ExitRule("none"), max_bars=10)

    assert build_signals(spec, [occ(make_series(), 20)]).signals[0].stop is None


def test_spec_roundtrip_is_stable() -> None:
    spec = StrategySpec(
        source="level_touch",
        groups=("level_touch", "x"),
        side="fade",
        quality_min=50,
        trends=("range", "uptrend"),
        since=START,
        stop=ExitRule("atr", 2.0),
        target=ExitRule("none"),
        max_bars=12,
    )

    data = spec.to_dict()

    assert StrategySpec.from_dict(data).to_dict() == data
    assert data["groups"] == ["level_touch", "x"] and data["trends"] == [
        "range",
        "uptrend",
    ]
    assert StrategySpec.from_dict({}) == StrategySpec()
