"""Индикаторы волатильности: ATR, полосы Боллинджера."""

import math
from collections import deque

from pydantic import BaseModel, Field

from trader_engine.indicators.base import BarInput, Indicator
from trader_engine.indicators.registry import register


class AtrParams(BaseModel):
    period: int = Field(default=14, ge=1, le=5000, description="Период, баров")


class BollingerParams(BaseModel):
    period: int = Field(default=20, ge=2, le=5000, description="Период, баров")
    mult: float = Field(
        default=2.0, gt=0, le=20, description="Множитель стандартного отклонения"
    )


@register
class Atr(Indicator):
    """ATR по Уайлдеру: первое значение — среднее первых ``period`` TR.

    TR первого бара — ``high − low``, у остальных учитывается разрыв к закрытию.
    ATR нужен движкам структуры (ZigZag, уровни) как масштаб цены.
    """

    name = "atr"
    title = "ATR — средний истинный диапазон"
    pane = "separate"
    Params = AtrParams

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        self._period = self.typed_params(AtrParams).period
        self._previous_close: float | None = None
        self._seed: list[float] = []
        self._value: float | None = None

    @property
    def warmup_bars(self) -> int:
        return self._period

    def update(self, bar: BarInput) -> tuple[float | None, ...]:
        if self._previous_close is None:
            true_range = bar.high - bar.low
        else:
            true_range = max(
                bar.high - bar.low,
                abs(bar.high - self._previous_close),
                abs(bar.low - self._previous_close),
            )
        self._previous_close = bar.close
        n = self._period
        if self._value is None:
            self._seed.append(true_range)
            if len(self._seed) < n:
                return (None,)
            self._value = sum(self._seed) / n
            self._seed = []
        else:
            self._value = (self._value * (n - 1) + true_range) / n
        return (self._value,)


@register
class Bollinger(Indicator):
    """Полосы Боллинджера: SMA ± ``mult`` × стандартное отклонение (ddof = 0)."""

    name = "bollinger"
    title = "Полосы Боллинджера"
    outputs = ("middle", "upper", "lower")
    Params = BollingerParams

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        p = self.typed_params(BollingerParams)
        self._period = p.period
        self._mult = p.mult
        self._window: deque[float] = deque(maxlen=self._period)

    @property
    def warmup_bars(self) -> int:
        return self._period

    def update(self, bar: BarInput) -> tuple[float | None, ...]:
        self._window.append(bar.close)
        if len(self._window) < self._period:
            return (None, None, None)
        mean = sum(self._window) / self._period
        variance = sum((x - mean) ** 2 for x in self._window) / self._period
        deviation = self._mult * math.sqrt(variance)
        return (mean, mean + deviation, mean - deviation)
