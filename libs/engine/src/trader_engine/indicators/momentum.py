"""Индикаторы импульса: RSI, ROC, MACD."""

from collections import deque

from pydantic import BaseModel, Field, model_validator

from trader_engine.indicators.base import BarInput, Indicator
from trader_engine.indicators.registry import register


class RsiParams(BaseModel):
    period: int = Field(default=14, ge=2, le=5000, description="Период, баров")


class RocParams(BaseModel):
    period: int = Field(default=10, ge=1, le=5000, description="Период, баров")


class MacdParams(BaseModel):
    fast: int = Field(default=12, ge=1, le=5000, description="Быстрая EMA")
    slow: int = Field(default=26, ge=2, le=5000, description="Медленная EMA")
    signal: int = Field(default=9, ge=1, le=5000, description="Сигнальная EMA")

    @model_validator(mode="after")
    def _fast_below_slow(self) -> "MacdParams":
        if self.fast >= self.slow:
            raise ValueError("fast должен быть меньше slow")
        return self


class _Ema:
    """EMA с затравкой SMA первых ``period`` значений (внутренний помощник)."""

    def __init__(self, period: int) -> None:
        self.period = period
        self.alpha = 2.0 / (period + 1)
        self.value: float | None = None
        self._seed: list[float] = []

    def update(self, x: float) -> float | None:
        if self.value is None:
            self._seed.append(x)
            if len(self._seed) < self.period:
                return None
            self.value = sum(self._seed) / self.period
            self._seed = []
        else:
            self.value = self.alpha * x + (1 - self.alpha) * self.value
        return self.value


@register
class Rsi(Indicator):
    """RSI по Уайлдеру: первые средние — простые за ``period`` изменений."""

    name = "rsi"
    title = "RSI — индекс относительной силы"
    pane = "separate"
    Params = RsiParams

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        self._period = self.typed_params(RsiParams).period
        self._previous: float | None = None
        self._gains: list[float] = []
        self._losses: list[float] = []
        self._avg_gain: float | None = None
        self._avg_loss: float | None = None

    @property
    def warmup_bars(self) -> int:
        return self._period + 1

    def update(self, bar: BarInput) -> tuple[float | None, ...]:
        previous, self._previous = self._previous, bar.close
        if previous is None:
            return (None,)
        change = bar.close - previous
        gain, loss = max(change, 0.0), max(-change, 0.0)
        n = self._period
        if self._avg_gain is None or self._avg_loss is None:
            self._gains.append(gain)
            self._losses.append(loss)
            if len(self._gains) < n:
                return (None,)
            self._avg_gain = sum(self._gains) / n
            self._avg_loss = sum(self._losses) / n
            self._gains, self._losses = [], []
        else:
            self._avg_gain = (self._avg_gain * (n - 1) + gain) / n
            self._avg_loss = (self._avg_loss * (n - 1) + loss) / n
        if self._avg_loss == 0:
            return (50.0 if self._avg_gain == 0 else 100.0,)
        rs = self._avg_gain / self._avg_loss
        return (100.0 - 100.0 / (1.0 + rs),)


@register
class Roc(Indicator):
    """ROC: процентное изменение цены закрытия за ``period`` баров."""

    name = "roc"
    title = "ROC — скорость изменения, %"
    pane = "separate"
    Params = RocParams

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        self._period = self.typed_params(RocParams).period
        self._window: deque[float] = deque(maxlen=self._period + 1)

    @property
    def warmup_bars(self) -> int:
        return self._period + 1

    def update(self, bar: BarInput) -> tuple[float | None, ...]:
        self._window.append(bar.close)
        if len(self._window) <= self._period:
            return (None,)
        base = self._window[0]
        if base == 0:
            return (0.0,)
        return (100.0 * (bar.close - base) / base,)


@register
class Macd(Indicator):
    """MACD: EMA(fast) − EMA(slow), сигнальная EMA этой линии и гистограмма.

    Все три выхода становятся валидными одновременно — когда прогрета сигнальная линия.
    """

    name = "macd"
    title = "MACD"
    pane = "separate"
    outputs = ("macd", "signal", "histogram")
    Params = MacdParams

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        p = self.typed_params(MacdParams)
        self._fast = _Ema(p.fast)
        self._slow = _Ema(p.slow)
        self._signal = _Ema(p.signal)
        self._warmup = p.slow + p.signal - 1

    @property
    def warmup_bars(self) -> int:
        return self._warmup

    def update(self, bar: BarInput) -> tuple[float | None, ...]:
        fast = self._fast.update(bar.close)
        slow = self._slow.update(bar.close)
        if fast is None or slow is None:
            return (None, None, None)
        macd = fast - slow
        signal = self._signal.update(macd)
        if signal is None:
            return (None, None, None)
        return (macd, signal, macd - signal)
