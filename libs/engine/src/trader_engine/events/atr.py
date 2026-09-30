"""ATR по Уайлдеру для движков событий (с состоянием в JSON)."""

from typing import Any

from trader_engine.indicators.base import BarInput


class WilderAtr:
    """Первое значение — среднее первых ``period`` TR; ``None`` до прогрева."""

    def __init__(self, period: int) -> None:
        self.period = period
        self.value: float | None = None
        self._previous_close: float | None = None
        self._seed: list[float] = []

    def update(self, bar: BarInput) -> float | None:
        if self._previous_close is None:
            true_range = bar.high - bar.low
        else:
            true_range = max(
                bar.high - bar.low,
                abs(bar.high - self._previous_close),
                abs(bar.low - self._previous_close),
            )
        self._previous_close = bar.close
        n = self.period
        if self.value is None:
            self._seed.append(true_range)
            if len(self._seed) >= n:
                self.value = sum(self._seed) / n
                self._seed = []
        else:
            self.value = (self.value * (n - 1) + true_range) / n
        return self.value

    def dump(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "previous_close": self._previous_close,
            "seed": self._seed,
        }

    def load(self, state: dict[str, Any]) -> None:
        self.value = state["value"]
        self._previous_close = state["previous_close"]
        self._seed = state["seed"]
