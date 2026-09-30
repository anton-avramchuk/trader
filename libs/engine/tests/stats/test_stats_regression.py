"""Регрессия статистики на фиксированном датасете (Old/New/Diff) и свойства исходов."""

import difflib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.events.dataset import fixed_bars
from tests.stats.test_outcomes import bar
from trader_engine.events import create, run_engine
from trader_engine.stats.outcomes import compute_outcomes
from trader_engine.stats.pipeline import (
    build_series,
    collect,
    compute_statistics,
)

SNAPSHOT = Path(__file__).parent / "snapshots" / "stats-v1.json"
ENGINES: list[tuple[str, dict[str, Any]]] = [
    ("levels", {"atr_period": 5}),
    ("double_triple", {"atr_period": 5, "tol_atr": 3.0, "min_height_atr": 0.5}),
    ("trendlines", {"atr_period": 5, "tol_atr": 3.0, "min_height_atr": 0.5}),
    ("range_breakout", {"atr_period": 5, "tol_atr": 3.0, "min_height_atr": 0.5}),
]


def rounded(value: object) -> Any:
    if isinstance(value, float):
        return round(value, 5)
    if isinstance(value, dict):
        mapping = cast(dict[str, object], value)
        return {key: rounded(item) for key, item in mapping.items()}
    if isinstance(value, list | tuple):
        return [rounded(item) for item in cast(list[object], value)]
    return value


def render() -> str:
    bars = fixed_bars()
    series = build_series("fixed:15m", bars, atr_period=5)
    logs = [(name, run_engine(create(name, params), bars)) for name, params in ENGINES]
    items = collect(series, logs)
    result = compute_statistics(
        items, horizons=(3, 8, 20), group_by="group", seed=7, min_baseline=5
    )
    return json.dumps(rounded(asdict(result)), sort_keys=True, indent=1) + "\n"


def test_statistics_match_the_saved_snapshot() -> None:
    new = render()
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(new, encoding="utf-8", newline="\n")
    assert SNAPSHOT.exists(), "нет снапшота: UPDATE_SNAPSHOTS=1"
    old = SNAPSHOT.read_text(encoding="utf-8")
    if old != new:
        diff = "".join(
            difflib.unified_diff(
                old.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile="Old",
                tofile="New",
                n=1,
            )
        )
        pytest.fail(f"статистика изменилась без нового алгоритма\n{diff[:4000]}")


def test_snapshot_is_not_trivial() -> None:
    data = json.loads(render())

    assert data["matched"] > 10
    assert len(data["buckets"]) >= 2


closes = st.lists(st.floats(1, 500), min_size=12, max_size=40)


@given(
    values=closes,
    tail=closes,
    horizon=st.integers(1, 6),
    bullish=st.booleans(),
)
@settings(max_examples=60, deadline=None)
def test_outcome_depends_only_on_bars_inside_the_horizon(
    values: list[float], tail: list[float], horizon: int, bullish: bool
) -> None:
    def rows(data: list[float]) -> list[Any]:
        return [bar(i, c, high=c * 1.01, low=c * 0.99) for i, c in enumerate(data)]

    entry = 5
    direction = "bullish" if bullish else "bearish"
    base = compute_outcomes(rows(values), entry, direction, 1.0, horizons=(horizon,))
    cut = entry + horizon + 1
    changed = compute_outcomes(
        rows([*values[:cut], *tail]), entry, direction, 1.0, horizons=(horizon,)
    )

    assert base[0].censored == (entry + horizon >= len(values))
    if not base[0].censored:
        assert base == changed


@given(values=closes, horizon=st.integers(1, 6))
@settings(max_examples=60, deadline=None)
def test_excursions_bound_the_return(values: list[float], horizon: int) -> None:
    data = [bar(i, c, high=c * 1.01, low=c * 0.99) for i, c in enumerate(values)]

    [out] = compute_outcomes(data, 2, "bullish", 1.0, horizons=(horizon,))

    if not out.censored:
        assert out.mfe_pct is not None and out.mae_pct is not None
        assert out.ret_pct is not None
        assert out.mfe_pct >= 0 and out.mae_pct >= 0
        assert -out.mae_pct - 1e-9 <= out.ret_pct <= out.mfe_pct + 1e-9
