"""Trend: направление и сила тренда на таймфрейме (ADR-0031, spec §16).

Тренд собирается из трёх независимых признаков:

- **структура** — состояние ``market_structure`` (HH+HL — вверх, LH+LL — вниз, иначе
  боковик) по подтверждённым swing ZigZag;
- **сторона EMA** — закрытие выше/ниже ``EMA(ema_period)`` не меньше чем на
  ``band_atr``·ATR (внутри полосы сторона не меняется — нет дребезга);
- **эффективность** — коэффициент Кауфмана ``|Δцены| / Σ|Δ|`` за ``er_period`` баров:
  у направленного движения он близок к 1, у шума к 0.

Тренд (``uptrend``/``downtrend``) объявляется, когда структура и сторона EMA
совпадают, а эффективность не ниже ``er_min``; иначе ``range``. Сила 0–100 — это
эффективность движения (полная при ``er ≥ 0.6``): структура и EMA отвечают за
направление, а не за силу. Корзины: слабый < 50 ≤ средний < 80 ≤ сильный; у боковика
корзина ``none``.

Событие ``trend`` (``confirmed``) выпускается при смене состояния или корзины силы;
последнее событие — текущая картина. ``since`` — открытие бара, с которого держится
текущее состояние.
"""

from typing import Any

from pydantic import BaseModel, Field

from trader_engine.events.atr import WilderAtr
from trader_engine.events.base import EventEngine
from trader_engine.events.registry import register
from trader_engine.events.structure import MarketStructure
from trader_engine.events.swing import ZigZagParams
from trader_engine.indicators.base import BarInput

KIND = "trend"
STRONG_FROM, MEDIUM_FROM = 80, 50
FULL_EFFICIENCY = 0.6


class TrendParams(ZigZagParams):
    atr_mult: float = Field(default=2.0, gt=0, le=50, description="Множитель ATR")
    ema_period: int = Field(default=50, ge=2, le=1000, description="Период EMA")
    band_atr: float = Field(
        default=0.25, ge=0, le=10, description="Полоса вокруг EMA, ATR"
    )
    er_period: int = Field(default=20, ge=2, le=500, description="Окно эффективности")
    er_min: float = Field(
        default=0.25, ge=0, le=1, description="Минимальная эффективность тренда"
    )


def bucket(strength: float) -> str:
    if strength >= STRONG_FROM:
        return "strong"
    return "medium" if strength >= MEDIUM_FROM else "weak"


@register
class Trend(EventEngine):
    name = "trend"
    title = "Тренд: направление и сила"
    Params = TrendParams

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        self._structure = MarketStructure(self.params)
        self._atr = WilderAtr(self.typed_params(TrendParams).atr_period)
        self._seed: list[float] = []
        self._ema: float | None = None
        self._closes: list[float] = []
        self._structure_state: str | None = None
        self._side = 0
        self._state: str | None = None
        self._bucket: str | None = None
        self._since: str | None = None

    def _update_ema(self, close: float) -> None:
        period = self.typed_params(TrendParams).ema_period
        if self._ema is None:
            self._seed.append(close)
            if len(self._seed) >= period:
                self._ema = sum(self._seed) / period
                self._seed = []
        else:
            alpha = 2.0 / (period + 1)
            self._ema = alpha * close + (1 - alpha) * self._ema

    def _efficiency(self) -> float | None:
        period = self.typed_params(TrendParams).er_period
        if len(self._closes) <= period:
            return None
        window = self._closes[-(period + 1) :]
        path = sum(abs(b - a) for a, b in zip(window, window[1:], strict=False))
        return abs(window[-1] - window[0]) / path if path > 0 else 0.0

    def on_bar(self, bar: BarInput) -> None:
        p = self.typed_params(TrendParams)
        for event in self._structure.update(bar):
            if event.kind == "trend_state":
                self._structure_state = event.payload["state"]
        atr = self._atr.update(bar)
        self._update_ema(bar.close)
        self._closes.append(bar.close)
        del self._closes[: -(p.er_period + 1)]
        efficiency = self._efficiency()
        if atr is None or self._ema is None or efficiency is None:
            return
        band = p.band_atr * atr
        if bar.close > self._ema + band:
            self._side = 1
        elif bar.close < self._ema - band:
            self._side = -1
        direction = {"uptrend": 1, "downtrend": -1}.get(self._structure_state or "", 0)
        if self._structure_state is None:
            return
        trending = direction != 0 and self._side == direction and efficiency >= p.er_min
        state = self._structure_state if trending else "range"
        strength = 100 * min(1.0, efficiency / FULL_EFFICIENCY)
        current = bucket(strength) if trending else "none"
        if state == self._state and current == self._bucket:
            return
        if state != self._state:
            self._since = bar.timestamp.isoformat()
        self._state, self._bucket = state, current
        self.emit(
            KIND,
            "confirmed",
            {
                "state": state,
                "strength": round(strength, 1),
                "bucket": current,
                "since": self._since,
                "structure": self._structure_state,
                "ema_side": self._side,
                "efficiency": round(efficiency, 3),
            },
        )

    def get_state(self) -> dict[str, Any]:
        return {
            "structure": self._structure.dump_state(),
            "atr": self._atr.dump(),
            "seed": self._seed,
            "ema": self._ema,
            "closes": self._closes,
            "structure_state": self._structure_state,
            "side": self._side,
            "state": self._state,
            "bucket": self._bucket,
            "since": self._since,
        }

    def set_state(self, state: dict[str, Any]) -> None:
        self._structure.load_state(state["structure"])
        self._atr.load(state["atr"])
        self._seed = state["seed"]
        self._ema = state["ema"]
        self._closes = state["closes"]
        self._structure_state = state["structure_state"]
        self._side = state["side"]
        self._state = state["state"]
        self._bucket = state["bucket"]
        self._since = state["since"]
