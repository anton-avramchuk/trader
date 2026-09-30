"""MTF-проекция: ступенька по available_at, без утечки формирующегося бара."""

import pytest
from hypothesis import HealthCheck, given, settings

from tests.indicators.helpers import bar_series, closes
from trader_engine.indicators import BarInput, create, run
from trader_engine.indicators.mtf import project, validate_source_timeframe

common = settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


def hourly(chart: list[BarInput]) -> list[BarInput]:
    """Часовые бары из полных четвёрок 15-минутных (неполная — формирующийся)."""
    bars: list[BarInput] = []
    for start in range(0, len(chart) - len(chart) % 4, 4):
        group = chart[start : start + 4]
        bars.append(
            BarInput(
                timestamp=group[0].timestamp,
                close_time=group[-1].close_time,
                open=group[0].open,
                high=max(b.high for b in group),
                low=min(b.low for b in group),
                close=group[-1].close,
                volume=sum(b.volume for b in group),
                trading_day=group[0].trading_day,
            )
        )
    return bars


class TestValidation:
    @pytest.mark.parametrize(
        ("chart", "source"),
        [("15m", "15m"), ("15m", "1h"), ("15m", "1w"), ("1h", "4h"), ("1d", "1w")],
    )
    def test_same_or_higher_source_is_allowed(self, chart: str, source: str) -> None:
        validate_source_timeframe(chart, source)

    @pytest.mark.parametrize(
        ("chart", "source"), [("1h", "15m"), ("1d", "4h"), ("1w", "15m")]
    )
    def test_lower_source_on_higher_chart_is_forbidden(
        self, chart: str, source: str
    ) -> None:
        with pytest.raises(ValueError, match="запрещён"):
            validate_source_timeframe(chart, source)

    def test_unknown_timeframe(self) -> None:
        with pytest.raises(ValueError, match="Неизвестный"):
            validate_source_timeframe("15m", "2h")


class TestProjection:
    chart = closes([float(v) for v in range(1, 25)])  # 24 бара по 15 минут = 6 часов

    def test_value_steps_when_the_higher_bar_closes(self) -> None:
        source = hourly(self.chart)
        series = run(create("sma", {"period": 2}), source)

        points = project(self.chart, source, series)

        # 1-й час закрывается на 4-м баре графика (индекс 3): SMA(2) ещё в прогреве.
        assert [p.source_timestamp is None for p in points[:3]] == [True] * 3
        assert all(p.source_timestamp == source[0].timestamp for p in points[3:7])
        assert not any(p.valid for p in points[:7])
        # 2-й час закрывается на индексе 7: первое валидное значение.
        assert points[7].valid
        assert points[7].values["value"] == series.values["value"][1]
        assert all(p.values == points[7].values for p in points[7:11])

    def test_forming_higher_bar_is_never_used(self) -> None:
        source = hourly(
            self.chart[:6]
        )  # 1 закрытый час + 2 бара следующего (формируется)
        series = run(create("sma", {"period": 1}), source)

        points = project(self.chart[:6], source, series)

        assert [p.source_timestamp == source[0].timestamp for p in points] == [
            False,
            False,
            False,
            True,
            True,
            True,
        ]

    def test_same_timeframe_is_the_bar_value_itself(self) -> None:
        series = run(create("sma", {"period": 3}), self.chart)

        points = project(self.chart, self.chart, series)

        assert [p.values["value"] for p in points] == series.values["value"]
        assert [p.valid for p in points] == [False, False] + [True] * 22

    def test_multiple_outputs_are_projected_together(self) -> None:
        source = hourly(self.chart)
        series = run(create("bollinger", {"period": 2, "mult": 1}), source)

        point = project(self.chart, source, series)[7]

        assert set(point.values) == {"middle", "upper", "lower"}
        assert point.valid and None not in point.values.values()

    @common
    @given(chart=bar_series(min_size=1, max_size=120))
    def test_no_leakage_available_at_never_exceeds_the_chart_bar_close(
        self, chart: list[BarInput]
    ) -> None:
        source = hourly(chart)
        series = run(create("sma", {"period": 2}), source)

        points = project(chart, source, series)

        assert len(points) == len(chart)
        previous = None
        for bar, point in zip(chart, points, strict=True):
            if point.available_at is None:
                assert not point.valid
                continue
            assert point.available_at <= bar.close_time  # значение уже известно
            assert previous is None or point.available_at >= previous  # ступенька
            previous = point.available_at
        # Число закрытых часов на каждом баре графика — целая часть (k + 1) / 4.
        for k, point in enumerate(points):
            closed = (k + 1) // 4
            assert (point.source_timestamp is None) == (closed == 0)
            if closed:
                assert point.source_timestamp == source[closed - 1].timestamp
