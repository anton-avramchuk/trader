"""Аналоги на цепочке настоящих движков: leakage, свойства, регрессия, детерминизм."""

from datetime import datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.events.dataset import fixed_bars, perturb_future
from trader_engine.analogues.library import build_candidates, formation_at
from trader_engine.analogues.search import AnalogueResult, find_analogues
from trader_engine.events import Event, create, run_engine
from trader_engine.indicators import BarInput
from trader_engine.stats.pipeline import SeriesOccurrence, build_series, collect

BARS = fixed_bars()
WINDOW = 24
HORIZONS = (3, 8)
ENGINES = ("levels", "double_triple")


def scaled_bars(scale: float) -> list[BarInput]:
    return [
        BarInput(
            b.timestamp,
            b.close_time,
            b.open * scale,
            b.high * scale,
            b.low * scale,
            b.close * scale,
            b.volume,
            b.trading_day,
        )
        for b in BARS
    ]


def events_of(bars: list[BarInput]) -> list[tuple[str, list[Event]]]:
    return [(name, run_engine(create(name), bars)) for name in ENGINES]


def items_at(
    bars: list[BarInput], as_of: datetime | None
) -> tuple[list[SeriesOccurrence], BarInput]:
    series = build_series("NG:15m", bars, as_of=as_of)
    events = [
        (name, [e for e in found if as_of is None or e.available_at <= as_of])
        for name, found in events_of(bars)
    ]
    return collect(series, events), series.bars[-1]


def search(
    bars: list[BarInput],
    as_of: datetime | None = None,
    *,
    k: int = 10,
    window: int = WINDOW,
    mode: str = "atr",
) -> AnalogueResult | None:
    items, _ = items_at(bars, as_of)
    series = items[0].series if items else build_series("NG:15m", bars, as_of=as_of)
    query = formation_at(
        series,
        len(series.bars) - 1,
        window=window,
        k=7,
        mode=mode,  # type: ignore[arg-type]
    )
    if query is None:
        return None
    candidates = build_candidates(
        items,
        as_of=series.bars[-1].close_time,
        window=window,
        k=7,
        mode=mode,  # type: ignore[arg-type]
        exclude=query,
    )
    return find_analogues(
        query,
        candidates,
        k=k,
        horizons=HORIZONS,
        unit="atr" if mode == "atr" else "pct",
    )


def signature(
    result: AnalogueResult,
) -> list[tuple[str, float, tuple[float | None, ...]]]:
    return [
        (
            m.candidate.occurrence.key,
            round(m.dtw.normalized_distance, 9),
            tuple(o.ret_atr for o in m.outcomes),
        )
        for m in result.matches
    ]


def test_fixture_chain_produces_matches() -> None:
    result = search(BARS)

    assert result is not None
    assert result.considered > 5
    assert result.matches


@pytest.mark.parametrize("cut", [150, 200, 250])
def test_result_at_as_of_does_not_depend_on_the_future(cut: int) -> None:
    as_of = BARS[cut].close_time
    clean = search(BARS, as_of)
    perturbed = search(perturb_future(BARS, cut + 1), as_of)

    assert clean is not None and perturbed is not None
    assert signature(clean) == signature(perturbed)
    assert clean.percentiles == perturbed.percentiles


@pytest.mark.parametrize("cut", [150, 250])
def test_matches_are_known_and_outside_the_query_window(cut: int) -> None:
    as_of = BARS[cut].close_time
    result = search(BARS, as_of)

    assert result is not None
    window_start = BARS[cut - WINDOW + 1].timestamp
    for match in result.matches:
        assert match.candidate.occurrence.available_at <= as_of
        entry_bar = match.candidate.series.bars[match.candidate.entry_index]
        assert entry_bar.close_time <= window_start


def test_outcomes_never_look_past_as_of() -> None:
    cut = 250
    as_of = BARS[cut].close_time
    result = search(BARS, as_of)

    assert result is not None
    for match in result.matches:
        entry = match.candidate.entry_index
        for outcome in match.outcomes:
            if entry + outcome.horizon > cut:
                assert outcome.censored


def test_search_is_deterministic() -> None:
    first, second = search(BARS), search(BARS)

    assert first is not None and second is not None
    assert signature(first) == signature(second)


@settings(max_examples=15, deadline=None)
@given(scale=st.floats(0.1, 50.0))
def test_percent_ranking_is_invariant_to_price_scale(scale: float) -> None:
    base = search(BARS, mode="percent")
    other = search(scaled_bars(scale), mode="percent")

    assert base is not None and other is not None
    assert [m.candidate.occurrence.key for m in base.matches] == [
        m.candidate.occurrence.key for m in other.matches
    ]
    for a, b in zip(base.matches, other.matches, strict=True):
        assert b.dtw.normalized_distance == pytest.approx(
            a.dtw.normalized_distance, rel=1e-6, abs=1e-9
        )


def test_atr_ranking_is_invariant_to_price_scale() -> None:
    base, other = search(BARS), search(scaled_bars(3.0))

    assert base is not None and other is not None
    assert [m.candidate.occurrence.key for m in base.matches] == [
        m.candidate.occurrence.key for m in other.matches
    ]
    for a, b in zip(base.matches, other.matches, strict=True):
        assert b.dtw.normalized_distance == pytest.approx(
            a.dtw.normalized_distance, rel=1e-6, abs=1e-9
        )


def test_regression_top_matches_on_fixed_series() -> None:
    result = search(BARS)

    assert result is not None
    head = [(key, distance) for key, distance, _ in signature(result)[:3]]
    assert head == REGRESSION_HEAD


REGRESSION_HEAD: list[tuple[str, float]] = [
    ("levels:84:158", 0.603653187),
    ("levels:62:105", 0.816854929),
    ("levels:121:231", 0.887448126),
]
