"""Online replay: индикатор «в моменте» совпадает с batch — нет look-ahead."""

from dataclasses import replace

import pytest
from pydantic import BaseModel

from tests.indicators.helpers import sample_bars
from trader_engine.indicators import (
    BarInput,
    Indicator,
    IndicatorSeries,
    available,
    create,
    run,
)
from trader_engine.indicators.verify import (
    MAX_REPORTED_MISMATCHES,
    sample_positions,
    verify_online,
    verify_registry,
)

BARS = sample_bars(120)


@pytest.mark.parametrize("plugin", available(), ids=[p.name for p in available()])
def test_every_registered_indicator_passes_online_replay(
    plugin: type[Indicator],
) -> None:
    """Сьют применяется ко всем плагинам реестра (новые подхватываются сами)."""
    report = verify_online(lambda: create(plugin.name), BARS)

    assert report.ok, report.mismatches[:3]
    assert report.positions_checked == len(BARS)
    assert report.values_checked == len(BARS) * len(plugin.outputs)


def test_registry_helper_covers_all_plugins() -> None:
    reports = verify_registry(BARS, max_positions=30)

    assert set(reports) == {p.name for p in available()}
    assert all(r.ok for r in reports.values())
    assert all(r.positions_checked <= 31 for r in reports.values())


class TestDetection:
    def test_tampered_batch_value_is_reported_with_position_and_values(self) -> None:
        def factory() -> Indicator:
            return create("sma", {"period": 5})

        batch = run(factory(), BARS)
        tampered = IndicatorSeries(
            {
                "value": [
                    v if i != 30 else 999.0 for i, v in enumerate(batch.values["value"])
                ]
            },
            batch.warmup_bars,
        )

        report = verify_online(factory, BARS, batch=tampered)

        assert not report.ok and report.mismatch_count == 1
        mismatch = report.mismatches[0]
        assert (mismatch.index, mismatch.output, mismatch.batch) == (30, "value", 999.0)
        assert mismatch.online == batch.values["value"][30]
        assert mismatch.timestamp == BARS[30].timestamp

    def test_hidden_state_shared_between_instances_is_caught(self) -> None:
        """Индикатор, зависящий не только от баров, ломает совпадение с batch."""
        calls = {"count": 0}

        class LeakyParams(BaseModel):
            pass

        class Leaky(Indicator):
            name = "leaky"
            title = "Утечка состояния"
            Params = LeakyParams

            @property
            def warmup_bars(self) -> int:
                return 1

            def update(self, bar: BarInput) -> tuple[float | None, ...]:
                calls["count"] += 1  # общий счётчик, а не состояние экземпляра
                return (float(calls["count"]),)

        report = verify_online(Leaky, BARS[:20])

        assert not report.ok and report.mismatch_count > 0

    def test_reported_mismatches_are_capped_but_counted(self) -> None:
        def factory() -> Indicator:
            return create("sma", {"period": 2})

        batch = run(factory(), BARS)
        broken = IndicatorSeries(
            {"value": [0.5 if v is not None else None for v in batch.values["value"]]},
            batch.warmup_bars,
        )

        report = verify_online(factory, BARS, batch=broken)

        assert report.mismatch_count == len(BARS) - 1
        assert len(report.mismatches) == MAX_REPORTED_MISMATCHES

    def test_a_single_moved_bar_changes_the_result_but_not_the_verdict(self) -> None:
        """Проверка не зависит от данных: другой ряд тоже проходит."""
        moved = [replace(bar, close=bar.close + 1) for bar in BARS]

        assert all(r.ok for r in verify_registry(moved, max_positions=20).values())


class TestSamplePositions:
    def test_small_input_is_checked_entirely(self) -> None:
        assert sample_positions(5, None) == [0, 1, 2, 3, 4]
        assert sample_positions(5, 10) == [0, 1, 2, 3, 4]
        assert sample_positions(0, 10) == []

    def test_large_input_is_sampled_evenly_and_includes_both_ends(self) -> None:
        positions = sample_positions(1000, 10)

        assert positions[0] == 0 and positions[-1] == 999
        assert positions == sorted(set(positions)) and len(positions) <= 11

    def test_single_position_budget_still_checks_the_last_bar(self) -> None:
        assert sample_positions(50, 1)[-1] == 49
