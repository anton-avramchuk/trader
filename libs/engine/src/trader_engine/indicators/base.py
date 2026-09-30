"""Контракт индикатора-плагина (ADR-0003, ADR-0010).

Индикатор — каузальная инкрементальная машина: ``update(bar)`` получает очередной
бар и возвращает по одному значению на каждый выход. Значение бара ``t``
зависит только от баров ``0..t``, поэтому batch (прогон по истории), replay и
инкрементальное дополнение хвоста — один и тот же код. До конца прогрева
(``warmup_bars``) значения ``None`` — невалидны и не используются.
"""

import copy
import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import ClassVar, Literal

from pydantic import BaseModel

from trader_engine.aggregation import Bar


@dataclass(frozen=True, slots=True)
class BarInput:
    """Бар на входе индикатора (цены — ``float``: индикаторы не денежные суммы)."""

    timestamp: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    trading_day: date

    @classmethod
    def from_bar(cls, bar: Bar) -> "BarInput":
        """Бар из агрегации (цены ``Decimal``) в форму для расчётов."""
        return cls(
            timestamp=bar.timestamp,
            close_time=bar.close_time,
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            volume=float(bar.volume),
            trading_day=bar.trading_day,
        )


Pane = Literal["price", "separate"]


class Indicator(ABC):
    """Базовый класс плагина. Подкласс задаёт метаданные и реализует ``update``.

    Новый индикатор добавляется декоратором ``@register`` без изменения ядра;
    общий контрактный тест подхватывает его автоматически и требует, чтобы у
    ``Params`` были значения по умолчанию для всех полей.
    """

    name: ClassVar[str]
    title: ClassVar[str]
    version: ClassVar[int] = 1
    # Где рисовать: поверх цены или в отдельной панели.
    pane: ClassVar[Pane] = "price"
    outputs: ClassVar[tuple[str, ...]] = ("value",)
    Params: ClassVar[type[BaseModel]]

    def __init__(self, params: BaseModel | None = None) -> None:
        self.params = params if params is not None else self.Params()

    @property
    @abstractmethod
    def warmup_bars(self) -> int:
        """Сколько баров нужно, чтобы первое значение стало валидным."""

    @abstractmethod
    def update(self, bar: BarInput) -> tuple[float | None, ...]:
        """Принять бар и вернуть значения выходов (``None`` — ещё не прогрето)."""

    def typed_params[P: BaseModel](self, model: type[P]) -> P:
        """Параметры как конкретная модель плагина (для проверки типов)."""
        assert isinstance(self.params, model)
        return self.params

    def snapshot(self) -> "Indicator":
        """Независимая копия состояния (для инкрементального продолжения)."""
        return copy.deepcopy(self)


@dataclass(slots=True)
class IndicatorSeries:
    """Значения индикатора по барам: ``values[выход][i]`` — для бара ``i``."""

    values: dict[str, list[float | None]]
    warmup_bars: int

    @property
    def length(self) -> int:
        for series in self.values.values():
            return len(series)
        return 0

    @property
    def valid_from(self) -> int:
        """Индекс первого валидного бара (равен ``warmup_bars - 1``)."""
        return max(self.warmup_bars - 1, 0)

    def at(self, index: int) -> dict[str, float | None]:
        return {name: series[index] for name, series in self.values.items()}


def run(indicator: Indicator, bars: Sequence[BarInput]) -> IndicatorSeries:
    """Прогнать машину по барам с нуля."""
    values: dict[str, list[float | None]] = {name: [] for name in indicator.outputs}
    for bar in bars:
        row = indicator.update(bar)
        if len(row) != len(indicator.outputs):
            raise ValueError(
                f"{indicator.name}: update вернул {len(row)} значений, "
                f"ожидалось {len(indicator.outputs)}"
            )
        for name, value in zip(indicator.outputs, row, strict=True):
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{indicator.name}: нечисловое значение {value}")
            values[name].append(value)
    return IndicatorSeries(values, indicator.warmup_bars)
