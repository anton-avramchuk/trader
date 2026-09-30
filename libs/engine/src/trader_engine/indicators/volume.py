"""Индикаторы объёма: Volume MA, Relative Volume, OBV."""

from collections import deque

from pydantic import BaseModel, Field

from trader_engine.indicators.base import BarInput, Indicator
from trader_engine.indicators.registry import register
from trader_engine.indicators.trend import NoParams


class VolumePeriodParams(BaseModel):
    period: int = Field(default=20, ge=1, le=5000, description="Период, баров")


@register
class VolumeMa(Indicator):
    name = "volume_ma"
    title = "Volume MA — скользящая средняя объёма"
    pane = "separate"
    Params = VolumePeriodParams

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        self._period = self.typed_params(VolumePeriodParams).period
        self._window: deque[float] = deque(maxlen=self._period)

    @property
    def warmup_bars(self) -> int:
        return self._period

    def update(self, bar: BarInput) -> tuple[float | None, ...]:
        self._window.append(bar.volume)
        if len(self._window) < self._period:
            return (None,)
        return (sum(self._window) / self._period,)


@register
class RelativeVolume(Indicator):
    """Relative Volume: объём бара / среднее объёма за ``period`` баров.

    Нулевое среднее (нет сделок в окне) даёт нейтральное значение 1.
    """

    name = "relative_volume"
    title = "Relative Volume — объём к среднему"
    pane = "separate"
    Params = VolumePeriodParams

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        self._period = self.typed_params(VolumePeriodParams).period
        self._window: deque[float] = deque(maxlen=self._period)

    @property
    def warmup_bars(self) -> int:
        return self._period

    def update(self, bar: BarInput) -> tuple[float | None, ...]:
        self._window.append(bar.volume)
        if len(self._window) < self._period:
            return (None,)
        average = sum(self._window) / self._period
        return (bar.volume / average if average > 0 else 1.0,)


@register
class Obv(Indicator):
    """OBV: объём со знаком движения цены, накопленный (1-й бар — его объём)."""

    name = "obv"
    title = "OBV — балансовый объём"
    pane = "separate"
    Params = NoParams

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        self._previous: float | None = None
        self._value = 0.0

    @property
    def warmup_bars(self) -> int:
        return 1

    def update(self, bar: BarInput) -> tuple[float | None, ...]:
        if self._previous is None:
            self._value = bar.volume
        elif bar.close > self._previous:
            self._value += bar.volume
        elif bar.close < self._previous:
            self._value -= bar.volume
        self._previous = bar.close
        return (self._value,)
