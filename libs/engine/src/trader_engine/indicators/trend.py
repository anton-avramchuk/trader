"""Индикаторы тренда: SMA, EMA, WMA, VWAP."""

from collections import deque

from pydantic import BaseModel, Field

from trader_engine.indicators.base import BarInput, Indicator
from trader_engine.indicators.registry import register


class PeriodParams(BaseModel):
    period: int = Field(default=20, ge=1, le=5000, description="Период, баров")


class NoParams(BaseModel):
    """У индикатора нет настраиваемых параметров."""


@register
class Sma(Indicator):
    name = "sma"
    title = "SMA — простая скользящая средняя"
    Params = PeriodParams

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        self._period = self.typed_params(PeriodParams).period
        self._window: deque[float] = deque(maxlen=self._period)

    @property
    def warmup_bars(self) -> int:
        return self._period

    def update(self, bar: BarInput) -> tuple[float | None, ...]:
        self._window.append(bar.close)
        if len(self._window) < self._period:
            return (None,)
        return (sum(self._window) / self._period,)


@register
class Ema(Indicator):
    """EMA: первое значение — SMA первых ``period`` баров, дальше рекурсия."""

    name = "ema"
    title = "EMA — экспоненциальная скользящая средняя"
    Params = PeriodParams

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        self._period = self.typed_params(PeriodParams).period
        self._alpha = 2.0 / (self._period + 1)
        self._seed: list[float] = []
        self._value: float | None = None

    @property
    def warmup_bars(self) -> int:
        return self._period

    def update(self, bar: BarInput) -> tuple[float | None, ...]:
        if self._value is None:
            self._seed.append(bar.close)
            if len(self._seed) < self._period:
                return (None,)
            self._value = sum(self._seed) / self._period
            self._seed = []
        else:
            self._value = self._alpha * bar.close + (1 - self._alpha) * self._value
        return (self._value,)


@register
class Wma(Indicator):
    """WMA: линейные веса 1…period, самый свежий бар весит больше всех."""

    name = "wma"
    title = "WMA — взвешенная скользящая средняя"
    Params = PeriodParams

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        self._period = self.typed_params(PeriodParams).period
        self._window: deque[float] = deque(maxlen=self._period)
        self._weights_sum = self._period * (self._period + 1) / 2

    @property
    def warmup_bars(self) -> int:
        return self._period

    def update(self, bar: BarInput) -> tuple[float | None, ...]:
        self._window.append(bar.close)
        if len(self._window) < self._period:
            return (None,)
        weighted = sum(w * v for w, v in enumerate(self._window, start=1))
        return (weighted / self._weights_sum,)


@register
class Vwap(Indicator):
    """VWAP с якорем на торговый день календаря: накопление сбрасывается со сменой дня.

    Цена бара — типичная ``(high + low + close) / 3``. Пока объём дня нулевой,
    значение равно типичной цене (индикатор не «невалиден» из-за бара без сделок).
    """

    name = "vwap"
    title = "VWAP — средневзвешенная по объёму (якорь: торговый день)"
    Params = NoParams

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        self._day: object | None = None
        self._price_volume = 0.0
        self._volume = 0.0

    @property
    def warmup_bars(self) -> int:
        return 1

    def update(self, bar: BarInput) -> tuple[float | None, ...]:
        if bar.trading_day != self._day:
            self._day = bar.trading_day
            self._price_volume = 0.0
            self._volume = 0.0
        typical = (bar.high + bar.low + bar.close) / 3
        self._price_volume += typical * bar.volume
        self._volume += bar.volume
        if self._volume <= 0:
            return (typical,)
        return (self._price_volume / self._volume,)
