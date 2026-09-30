"""Реестр плагинов и LRU-кэш индикаторов."""

import pytest
from pydantic import BaseModel, Field

from tests.indicators.helpers import closes
from trader_engine.indicators import (
    BarInput,
    Indicator,
    IndicatorCache,
    available,
    create,
    describe,
    get,
    params_hash,
    register,
    run,
    unregister,
)


class DoublerParams(BaseModel):
    k: float = Field(default=2.0, gt=0)


class TestRegistry:
    def test_builtin_indicator_is_available_by_name(self) -> None:
        assert "sma" in [p.name for p in available()]
        assert get("sma").name == "sma"

    def test_unknown_indicator_lists_known_ones(self) -> None:
        with pytest.raises(KeyError, match="sma"):
            get("nope")

    def test_bad_params_give_a_readable_error(self) -> None:
        with pytest.raises(ValueError, match="period"):
            create("sma", {"period": 0})
        with pytest.raises(ValueError, match="period"):
            create("sma", {"period": "abc"})

    def test_params_hash_is_stable_and_depends_on_values(self) -> None:
        assert params_hash(create("sma", {"period": 10})) == params_hash(
            create("sma", {"period": 10})
        )
        assert params_hash(create("sma", {"period": 10})) != params_hash(
            create("sma", {"period": 11})
        )

    def test_describe_gives_form_schema_and_defaults(self) -> None:
        info = describe(get("sma"))

        assert info["defaults"] == {"period": 20}
        assert info["params_schema"]["properties"]["period"]["minimum"] == 1
        assert info["warmup_bars"] == 20

    def test_new_plugin_is_added_without_touching_the_core(self) -> None:
        @register
        class Doubler(Indicator):
            name = "test_doubler"
            title = "Тестовый"
            Params = DoublerParams

            @property
            def warmup_bars(self) -> int:
                return 1

            def update(self, bar: BarInput) -> tuple[float | None, ...]:
                return (bar.close * self.typed_params(DoublerParams).k,)

        try:
            series = run(create("test_doubler", {"k": 3}), closes([1.0, 2.0]))

            assert series.values["value"] == [3.0, 6.0]
            with pytest.raises(ValueError, match="уже зарегистрирован"):
                register(Doubler)
        finally:
            unregister("test_doubler")


class TestCache:
    bars = closes([float(v) for v in range(1, 41)])

    def test_repeat_request_is_a_hit(self) -> None:
        cache = IndicatorCache()
        sma = create("sma", {"period": 5})

        first = cache.compute("ds1", "1h", sma, self.bars)
        second = cache.compute("ds1", "1h", sma, self.bars)

        assert first.values == second.values
        assert (cache.stats.misses, cache.stats.hits) == (1, 1)
        assert cache.stats.bars_computed == 40

    def test_returned_series_are_copies(self) -> None:
        cache = IndicatorCache()
        sma = create("sma", {"period": 5})

        cache.compute("ds", "1h", sma, self.bars).values["value"].clear()
        again = cache.compute("ds", "1h", sma, self.bars)

        assert len(again.values["value"]) == 40

    def test_new_bars_compute_only_the_tail(self) -> None:
        cache = IndicatorCache()
        sma = create("sma", {"period": 5})
        cache.compute("ds", "1h", sma, self.bars[:30])

        extended = cache.compute("ds", "1h", sma, self.bars)

        assert cache.stats.extends == 1
        assert cache.stats.bars_computed == 30 + 10
        assert extended.values == run(create("sma", {"period": 5}), self.bars).values

    def test_key_separates_dataset_params_and_source_tf(self) -> None:
        cache = IndicatorCache()
        sma = create("sma", {"period": 5})

        cache.compute("ds1", "1h", sma, self.bars)
        cache.compute("ds2", "1h", sma, self.bars)
        cache.compute("ds1", "4h", sma, self.bars)
        cache.compute("ds1", "1h", create("sma", {"period": 6}), self.bars)

        assert cache.stats.misses == 4 and len(cache) == 4

    def test_changed_history_is_recomputed_not_treated_as_a_continuation(
        self,
    ) -> None:
        cache = IndicatorCache()
        sma = create("sma", {"period": 5})
        cache.compute("ds", "1h", sma, self.bars[:30])
        rescaled = closes([v * 2 for v in range(1, 41)])  # ролл сменил масштаб прошлого

        result = cache.compute("ds", "1h", sma, rescaled)

        assert cache.stats.misses == 2 and cache.stats.extends == 0
        assert result.values == run(create("sma", {"period": 5}), rescaled).values

    def test_shorter_request_than_cached_is_recomputed(self) -> None:
        cache = IndicatorCache()
        sma = create("sma", {"period": 5})
        cache.compute("ds", "1h", sma, self.bars)

        short = cache.compute("ds", "1h", sma, self.bars[:20])

        assert cache.stats.misses == 2
        assert len(short.values["value"]) == 20

    def test_least_recently_used_entry_is_evicted(self) -> None:
        cache = IndicatorCache(maxsize=2)
        sma = create("sma", {"period": 5})

        cache.compute("a", "1h", sma, self.bars)
        cache.compute("b", "1h", sma, self.bars)
        cache.compute("a", "1h", sma, self.bars)  # a свежее b
        cache.compute("c", "1h", sma, self.bars)  # вытесняет b
        before = cache.stats.misses
        cache.compute("a", "1h", sma, self.bars)
        cache.compute("b", "1h", sma, self.bars)

        assert cache.stats.misses == before + 1  # a в кэше, b — нет

    def test_empty_bars(self) -> None:
        series = IndicatorCache().compute("ds", "1h", create("sma"), [])

        assert series.values == {"value": []} and series.length == 0
