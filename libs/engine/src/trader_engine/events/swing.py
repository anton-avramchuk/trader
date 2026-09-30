"""Swing points: ZigZag (по ATR или в процентах) и fixed-window (ADR-0012).

Оба движка каузальны. ZigZag ведёт «кандидата» — экстремум текущего колена: новый
экстремум — ревизия кандидата (``revised``), закрытие бара на расстоянии не меньше
порога от экстремума — подтверждение (``confirmed``, ``confirmed_at = close_time``
этого бара) и начало колена в обратную сторону (новый кандидат — ``detected``).
Fixed-window подтверждает экстремум окна, когда справа набралось ``window`` баров.
Событие ``swing``; ``payload.timestamp`` — открытие бара-экстремума, а время знания
о нём — ``available_at`` (закрытие бара подтверждения).
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from trader_engine.events.base import EventEngine
from trader_engine.events.registry import register
from trader_engine.indicators.base import BarInput

KIND = "swing"
HIGH, LOW = "high", "low"


class ZigZagParams(BaseModel):
    threshold: Literal["atr", "percent"] = Field(
        default="atr", description="Порог разворота: кратный ATR или в процентах"
    )
    atr_period: int = Field(default=14, ge=1, le=5000, description="Период ATR")
    atr_mult: float = Field(default=1.5, gt=0, le=50, description="Множитель ATR")
    percent: float = Field(default=1.0, gt=0, le=100, description="Порог, % цены")


class FixedWindowParams(BaseModel):
    window: int = Field(default=5, ge=1, le=500, description="Баров с каждой стороны")


@register
class ZigZag(EventEngine):
    """ZigZag: по умолчанию порог 1.5 × ATR14 того же таймфрейма.

    Пока порог не известен (ATR не прогрет), движок только копит экстремум. Первое
    колено определяется первым подтверждённым разворотом от бегущего максимума.
    """

    name = "zigzag"
    title = "ZigZag (swing points)"
    Params = ZigZagParams

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        self._direction = 0  # +1 — ищем максимум, −1 — минимум, 0 — не начали
        self._ext: dict[str, Any] = {}
        self._leg: list[list[Any]] = []  # [open, high, low] с бара экстремума
        self._chain: int | None = None  # последнее событие цепочки кандидата
        self._prev_close: float | None = None
        self._seed: list[float] = []
        self._atr: float | None = None

    def _update_atr(self, bar: BarInput) -> None:
        n = self.typed_params(ZigZagParams).atr_period
        if self._prev_close is None:
            true_range = bar.high - bar.low
        else:
            true_range = max(
                bar.high - bar.low,
                abs(bar.high - self._prev_close),
                abs(bar.low - self._prev_close),
            )
        self._prev_close = bar.close
        if self._atr is None:
            self._seed.append(true_range)
            if len(self._seed) >= n:
                self._atr = sum(self._seed) / n
                self._seed = []
        else:
            self._atr = (self._atr * (n - 1) + true_range) / n

    def _threshold(self, price: float) -> float | None:
        p = self.typed_params(ZigZagParams)
        if p.threshold == "atr":
            return None if self._atr is None else p.atr_mult * self._atr
        return price * p.percent / 100

    def _payload(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        p = self.typed_params(ZigZagParams)
        payload: dict[str, Any] = {
            "type": HIGH if self._direction > 0 else LOW,
            "price": self._ext["price"],
            "timestamp": self._ext["ts"],
            "method": f"zigzag_{p.threshold}",
        }
        return payload | (extra or {})

    def on_bar(self, bar: BarInput) -> None:
        self._update_atr(bar)
        opened = bar.timestamp.isoformat()
        if self._direction == 0:
            self._direction = 1
            self._ext = {"price": bar.high, "ts": opened}
        d = self._direction
        best = bar.high if d > 0 else bar.low
        if best * d > self._ext["price"] * d:
            self._ext = {"price": best, "ts": opened}
            self._leg = []
            if self._chain is not None:
                self._chain = self.emit(
                    KIND, "revised", self._payload(), revises=self._chain
                ).seq
        self._leg.append([opened, bar.high, bar.low])
        limit = self._threshold(self._ext["price"])
        if limit is not None and (self._ext["price"] - bar.close) * d >= limit:
            self._confirm(limit)

    def _confirm(self, limit: float) -> None:
        if self._chain is None:
            self._chain = self.emit(KIND, "detected", self._payload()).seq
        self.emit(
            KIND, "confirmed", self._payload({"threshold": limit}), revises=self._chain
        )
        # Новое колено: экстремум — самая дальняя точка в обратную сторону в ноге
        # (при равенстве — самая ранняя).
        d = self._direction
        pick = min(
            range(len(self._leg)),
            key=lambda i: self._leg[i][2] if d > 0 else -self._leg[i][1],
        )
        ts, high, low = self._leg[pick]
        self._direction = -d
        self._ext = {"price": low if d > 0 else high, "ts": ts}
        self._leg = self._leg[pick:]
        self._chain = self.emit(KIND, "detected", self._payload()).seq

    def get_state(self) -> dict[str, Any]:
        return {
            "direction": self._direction,
            "ext": self._ext,
            "leg": self._leg,
            "chain": self._chain,
            "prev_close": self._prev_close,
            "seed": self._seed,
            "atr": self._atr,
        }

    def set_state(self, state: dict[str, Any]) -> None:
        self._direction = state["direction"]
        self._ext = state["ext"]
        self._leg = state["leg"]
        self._chain = state["chain"]
        self._prev_close = state["prev_close"]
        self._seed = state["seed"]
        self._atr = state["atr"]


@register
class FixedWindow(EventEngine):
    """Максимум/минимум, который не перекрыт ``window`` барами слева и справа.

    Экстремум подтверждается закрытием бара ``window`` позже. Слева максимум строго
    выше соседей, справа — не ниже (на плато берётся первый бар).
    """

    name = "swing_fixed"
    title = "Swing points: fixed-window"
    Params = FixedWindowParams

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        self._bars: list[list[Any]] = []  # [open, high, low]

    def on_bar(self, bar: BarInput) -> None:
        k = self.typed_params(FixedWindowParams).window
        self._bars.append([bar.timestamp.isoformat(), bar.high, bar.low])
        if len(self._bars) < 2 * k + 1:
            return
        del self._bars[: len(self._bars) - (2 * k + 1)]
        ts, high, low = self._bars[k]
        left, right = self._bars[:k], self._bars[k + 1 :]
        if high > max(b[1] for b in left) and high >= max(b[1] for b in right):
            self.emit(KIND, "confirmed", self._payload(HIGH, high, ts, k))
        if low < min(b[2] for b in left) and low <= min(b[2] for b in right):
            self.emit(KIND, "confirmed", self._payload(LOW, low, ts, k))

    @staticmethod
    def _payload(kind: str, price: float, ts: str, window: int) -> dict[str, Any]:
        return {
            "type": kind,
            "price": price,
            "timestamp": ts,
            "method": "fixed_window",
            "window": window,
        }

    def get_state(self) -> dict[str, Any]:
        return {"bars": self._bars}

    def set_state(self, state: dict[str, Any]) -> None:
        self._bars = state["bars"]
