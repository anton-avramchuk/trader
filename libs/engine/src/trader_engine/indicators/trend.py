"""Индикаторы тренда: SMA (остальные добавляются задачей #26)."""

from collections import deque

from pydantic import BaseModel, Field

from trader_engine.indicators.base import BarInput, Indicator
from trader_engine.indicators.registry import register


class PeriodParams(BaseModel):
    period: int = Field(default=20, ge=1, le=5000, description="Период, баров")


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
