"""Контракт всех зарегистрированных индикаторов (подхватывается автоматически).

Проверяет каузальность и прогрев: значение бара ``t`` не зависит от баров после
``t`` (префикс-инвариантность ``f(data[:t])[-1] == f(data)[t]``), до конца прогрева
значения ``None``, после — конечные числа, продолжение хвоста из кэша даёт то же,
что расчёт с нуля.
"""

import math

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.indicators.helpers import bar_series, sample_bars
from trader_engine.indicators import (
    BarInput,
    Indicator,
    IndicatorCache,
    available,
    create,
    describe,
    run,
)

PLUGINS = available()
common = settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


@pytest.mark.parametrize("plugin", PLUGINS, ids=[p.name for p in PLUGINS])
class TestPluginContract:
    def test_metadata_and_default_params(self, plugin: type[Indicator]) -> None:
        info = describe(plugin)

        assert info["name"] == plugin.name and info["version"] >= 1
        assert info["pane"] in ("price", "separate")
        assert info["outputs"] and info["warmup_bars"] >= 1
        assert info["params_schema"]["type"] == "object"
        assert create(plugin.name).params.model_dump(mode="json") == info["defaults"]

    @common
    @given(bars=bar_series(max_size=150))
    def test_warmup_marks_values_invalid_and_valid_values_are_finite(
        self, plugin: type[Indicator], bars: list[BarInput]
    ) -> None:
        indicator = create(plugin.name)

        series = run(indicator, bars)

        assert series.length == len(bars)
        for name in indicator.outputs:
            for index, value in enumerate(series.values[name]):
                if index < series.valid_from:
                    assert value is None, (name, index)
                else:
                    assert value is not None and math.isfinite(value), (name, index)

    @common
    @given(bars=bar_series(min_size=2, max_size=150), data=st.data())
    def test_prefix_invariance(
        self, plugin: type[Indicator], bars: list[BarInput], data: st.DataObject
    ) -> None:
        """Значение на баре t не зависит от будущих баров."""
        batch = run(create(plugin.name), bars)
        t = data.draw(st.integers(min_value=0, max_value=len(bars) - 1))

        prefix = run(create(plugin.name), bars[: t + 1])

        assert prefix.at(t) == batch.at(t)
        assert {n: v[: t + 1] for n, v in batch.values.items()} == prefix.values

    @common
    @given(bars=bar_series(min_size=2, max_size=150), data=st.data())
    def test_extending_the_tail_from_cache_equals_full_recompute(
        self, plugin: type[Indicator], bars: list[BarInput], data: st.DataObject
    ) -> None:
        cut = data.draw(st.integers(min_value=1, max_value=len(bars) - 1))
        cache = IndicatorCache()
        indicator = create(plugin.name)

        cache.compute("ds", "15m", indicator, bars[:cut])
        extended = cache.compute("ds", "15m", indicator, bars)

        assert cache.stats.extends == 1
        assert extended.values == run(create(plugin.name), bars).values

    def test_machine_state_is_independent_after_snapshot(
        self, plugin: type[Indicator]
    ) -> None:
        bars = sample_bars()
        machine = create(plugin.name)
        run(machine, bars[:30])
        copy = machine.snapshot()

        run(machine, bars[30:])
        tail_from_copy = run(copy, bars[30:])
        tail_from_fresh = run(create(plugin.name), bars)

        for name in machine.outputs:
            assert tail_from_copy.values[name] == tail_from_fresh.values[name][30:]
