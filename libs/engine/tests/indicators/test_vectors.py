"""Проверенные тест-векторы индикаторов (значения посчитаны независимо от кода)."""

import math
from typing import Any

import pytest

from tests.indicators.helpers import closes, make_bars
from trader_engine.indicators import BarInput, create, run

approx = pytest.approx


def values(
    name: str,
    bars: list[BarInput],
    params: dict[str, Any] | None = None,
    output: str = "value",
) -> list[float | None]:
    return run(create(name, params), bars).values[output]


class TestTrend:
    def test_sma(self) -> None:
        assert values("sma", closes([1, 2, 3, 4, 5]), {"period": 3}) == [
            None,
            None,
            2.0,
            3.0,
            4.0,
        ]

    def test_ema_is_seeded_by_sma_then_recursive(self) -> None:
        # alpha = 2 / (3 + 1) = 0,5; затравка — SMA(1, 2, 3) = 2
        assert values("ema", closes([1, 2, 3, 4, 5, 6]), {"period": 3}) == [
            None,
            None,
            2.0,
            3.0,
            4.0,
            5.0,
        ]

    def test_ema_period_one_follows_the_price(self) -> None:
        assert values("ema", closes([3, 1, 4]), {"period": 1}) == [3.0, 1.0, 4.0]

    def test_wma_weights_recent_bars_more(self) -> None:
        result = values("wma", closes([1, 2, 3, 4]), {"period": 3})

        assert result[:2] == [None, None]
        assert result[2] == approx((1 * 1 + 2 * 2 + 3 * 3) / 6)
        assert result[3] == approx((1 * 2 + 2 * 3 + 3 * 4) / 6)

    def test_vwap_is_anchored_to_the_trading_day(self) -> None:
        rows = [(10.0, 12.0, 8.0, 10.0, 100.0), (20.0, 20.0, 20.0, 20.0, 300.0)]
        rows += [
            (5.0, 5.0, 5.0, 5.0, 50.0)
        ] * 22  # добираем до конца первого дня (24 бара)
        rows += [(30.0, 30.0, 30.0, 30.0, 10.0)]  # первый бар нового дня
        bars = make_bars(rows)

        result = values("vwap", bars)

        # типичная цена 1-го бара = (12 + 8 + 10) / 3 = 10
        assert result[0] == approx(10.0)
        assert result[1] == approx((10 * 100 + 20 * 300) / 400)
        assert result[24] == approx(30.0)  # накопление сброшено новым торговым днём

    def test_vwap_with_zero_volume_uses_typical_price(self) -> None:
        assert values("vwap", make_bars([(1.0, 3.0, 2.0, 2.0, 0.0)])) == [approx(7 / 3)]


class TestMomentum:
    # Ряд из ChartSchool «RSI» (StockCharts).
    RSI_CLOSES = [
        44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
        45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64,
        46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03, 44.18, 44.22, 44.57,
        43.42, 42.66, 43.13,
    ]  # fmt: skip

    @staticmethod
    def reference_rsi(closes_: list[float], period: int) -> list[float]:
        """Независимая реализация Уайлдера (списки, без общего с плагином кода)."""
        gains = [max(b - a, 0.0) for a, b in zip(closes_, closes_[1:], strict=False)]
        losses = [max(a - b, 0.0) for a, b in zip(closes_, closes_[1:], strict=False)]
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        out = [100 - 100 / (1 + avg_gain / avg_loss)]
        for gain, loss in zip(gains[period:], losses[period:], strict=True):
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period
            out.append(100 - 100 / (1 + avg_gain / avg_loss))
        return out

    def test_rsi_first_value_by_hand(self) -> None:
        # Первые 14 изменений: прирост 3,34, убыток 1,40 → RS = 3,34 / 1,40.
        result = values("rsi", closes(self.RSI_CLOSES), {"period": 14})

        assert result[:14] == [None] * 14
        assert result[14] == approx(100 - 100 / (1 + 3.34 / 1.40), abs=1e-9)
        assert result[14] == approx(70.4641, abs=1e-4)

    def test_rsi_matches_the_reference_implementation(self) -> None:
        result = values("rsi", closes(self.RSI_CLOSES), {"period": 14})

        expected = self.reference_rsi(self.RSI_CLOSES, 14)
        assert len(result[14:]) == len(expected) == 19
        for got, want in zip(result[14:], expected, strict=True):
            assert got == approx(want, abs=1e-9)

    def test_rsi_bounds_for_monotone_series(self) -> None:
        up = values("rsi", closes([float(i) for i in range(1, 30)]), {"period": 5})
        down = values(
            "rsi", closes([float(i) for i in range(30, 1, -1)]), {"period": 5}
        )
        flat = values("rsi", closes([5.0] * 12), {"period": 5})

        assert up[-1] == 100.0 and down[-1] == approx(0.0) and flat[-1] == 50.0

    def test_roc_is_percent_change_over_the_period(self) -> None:
        result = values("roc", closes([10, 11, 12, 13, 14]), {"period": 2})

        assert result[:2] == [None, None]
        assert result[2] == approx(20.0)
        assert result[3] == approx(100 * (13 - 11) / 11)

    def test_macd_of_a_constant_series_is_zero(self) -> None:
        bars = closes([100.0] * 60)

        for output in ("macd", "signal", "histogram"):
            result = values("macd", bars, {"fast": 3, "slow": 6, "signal": 4}, output)
            assert result[:8] == [None] * 8  # прогрев: slow + signal - 1 = 9 баров
            assert all(v == approx(0.0, abs=1e-12) for v in result[8:])

    def test_macd_matches_an_independent_reference(self) -> None:
        prices = [
            10.0,
            11.0,
            12.5,
            12.0,
            13.0,
            14.5,
            14.0,
            15.5,
            16.0,
            15.0,
            17.0,
            18.0,
        ]
        fast, slow, signal = 2, 3, 2

        def ema(series: list[float], period: int) -> list[float]:
            out = [sum(series[:period]) / period]
            for x in series[period:]:
                out.append(2 / (period + 1) * x + (1 - 2 / (period + 1)) * out[-1])
            return out

        fast_ema, slow_ema = ema(prices, fast), ema(prices, slow)
        macd = [f - s for f, s in zip(fast_ema[slow - fast :], slow_ema, strict=True)]
        sig = ema(macd, signal)
        offset = slow - 1 + signal - 1

        result = run(
            create("macd", {"fast": fast, "slow": slow, "signal": signal}),
            closes(prices),
        ).values

        for i, s in enumerate(sig):
            m = macd[i + signal - 1]
            assert result["macd"][offset + i] == approx(m)
            assert result["signal"][offset + i] == approx(s)
            assert result["histogram"][offset + i] == approx(m - s)

    def test_macd_requires_fast_below_slow(self) -> None:
        with pytest.raises(ValueError, match="fast"):
            create("macd", {"fast": 26, "slow": 12})


class TestVolatilityAndVolume:
    def test_atr_uses_wilder_smoothing_and_gap_aware_true_range(self) -> None:
        # (high, low, close): TR = 2, 2, 3
        bars = make_bars([(9, 10, 8, 9, 1), (10, 11, 9, 10, 1), (11, 13, 10, 11.5, 1)])

        result = values("atr", bars, {"period": 2})

        assert result[0] is None
        assert result[1] == approx(2.0)  # среднее первых двух TR
        assert result[2] == approx((2.0 * 1 + 3.0) / 2)

    def test_true_range_includes_the_gap_from_previous_close(self) -> None:
        bars = make_bars([(10, 10, 10, 10, 1), (20, 21, 20, 20, 1)])

        result = values("atr", bars, {"period": 2})

        # TR1 = 0, TR2 = max(1, |21 - 10|, |20 - 10|) = 11
        assert result[1] == approx(5.5)

    def test_bollinger_uses_population_deviation(self) -> None:
        result = run(
            create("bollinger", {"period": 5, "mult": 2}), closes([1, 2, 3, 4, 5])
        ).values

        sigma = math.sqrt(2.0)
        assert result["middle"][4] == approx(3.0)
        assert result["upper"][4] == approx(3.0 + 2 * sigma)
        assert result["lower"][4] == approx(3.0 - 2 * sigma)
        assert result["middle"][:4] == [None] * 4

    def test_volume_ma_and_relative_volume(self) -> None:
        bars = make_bars([(1, 1, 1, 1, v) for v in (10.0, 20.0, 30.0)])

        assert values("volume_ma", bars, {"period": 3}) == [None, None, 20.0]
        assert values("relative_volume", bars, {"period": 3})[2] == approx(1.5)

    def test_relative_volume_of_a_silent_window_is_neutral(self) -> None:
        bars = make_bars([(1, 1, 1, 1, 0.0)] * 3)

        assert values("relative_volume", bars, {"period": 3})[2] == 1.0

    def test_obv_adds_on_up_subtracts_on_down_and_keeps_on_flat(self) -> None:
        bars = make_bars(
            [(c, c, c, c, v) for c, v in [(10, 5), (11, 3), (10, 4), (10, 2), (12, 6)]]
        )

        assert values("obv", bars) == [5.0, 8.0, 4.0, 4.0, 10.0]
