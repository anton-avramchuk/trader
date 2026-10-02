"""Fibonacci Engine: автоматическая сетка на значимой ноге (ADR-0030, spec §23).

Сетка строится по подтверждённым swing ZigZag, а не по бегущему колену: в окне из
последних ``window`` подтверждённых swing берётся размах «минимум ↔ максимум», нога —
от более раннего экстремума к более позднему. Нога должна быть не короче
``min_leg_mult`` порогов ZigZag, иначе сетка не строится (мелкие колебания шума не
дают сетку). Откат считается от конца ноги, то есть показывает, где может закончиться
текущая коррекция. Новая сетка — ``detected``; если нога выросла от того же начала —
``revised``. Уровни: retracement 23.6/38.2/50/61.8/78.6 и extension 100/127.2/161.8.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from trader_engine.events.base import EventEngine
from trader_engine.events.registry import register
from trader_engine.events.swing import HIGH, ZigZag, ZigZagParams
from trader_engine.indicators.base import BarInput

KIND = "fib_grid"
RETRACEMENTS = (0.236, 0.382, 0.5, 0.618, 0.786)
EXTENSIONS = (1.0, 1.272, 1.618)


def fib_levels(start: float, end: float) -> dict[str, dict[str, float]]:
    """Уровни ноги ``start → end``.

    Retracement — откат от ``end`` в сторону ``start``; extension — продолжение ноги
    (100 % — сам ``end``).
    """
    span = end - start
    return {
        "retracement": {f"{r * 100:g}": end - r * span for r in RETRACEMENTS},
        "extension": {f"{e * 100:g}": start + e * span for e in EXTENSIONS},
    }


def grid_payload(
    start: dict[str, Any], end: dict[str, Any], method: str
) -> dict[str, Any]:
    return {
        "start": start,
        "end": end,
        "direction": "up" if end["price"] > start["price"] else "down",
        "method": method,
        "auto": True,
        **fib_levels(start["price"], end["price"]),
    }


class FibonacciParams(ZigZagParams):
    atr_mult: float = Field(default=2.0, gt=0, le=50, description="Множитель ATR")
    window: int = Field(
        default=100, ge=2, le=500, description="Сколько последних swing учитывать"
    )
    min_leg_mult: float = Field(
        default=2.0, gt=0, le=50, description="Минимум ноги в порогах ZigZag"
    )


def point(payload: dict[str, Any]) -> dict[str, Any]:
    return {"price": payload["price"], "timestamp": payload["timestamp"]}


@register
class Fibonacci(EventEngine):
    name = "fibonacci"
    title = "Fibonacci: автоматическая сетка"
    version = 4
    Params = FibonacciParams

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        self._zigzag = ZigZag(self.params)
        self._swings: list[dict[str, Any]] = []  # подтверждённые, последние ``window``
        self._method = ""
        self._grid: int | None = None  # последнее событие цепочки сетки
        self._shown: list[dict[str, Any]] | None = None  # (start, end) в событии

    def on_bar(self, bar: BarInput) -> None:
        for event in self._zigzag.update(bar):
            self._method = event.payload["method"]
            if event.status == "confirmed":
                self._confirmed(event.payload)

    def _confirmed(self, payload: dict[str, Any]) -> None:
        p = self.typed_params(FibonacciParams)
        self._swings.append({**point(payload), "type": payload["type"]})
        del self._swings[: -p.window]
        highs = [s for s in self._swings if s["type"] == HIGH]
        lows = [s for s in self._swings if s["type"] != HIGH]
        if not highs or not lows:
            return
        top = max(highs, key=lambda s: (s["price"], _ts(s)))
        bottom = min(lows, key=lambda s: (s["price"], -_ts(s)))
        if top["price"] - bottom["price"] < p.min_leg_mult * payload["threshold"]:
            return
        start, end = (bottom, top) if _ts(bottom) < _ts(top) else (top, bottom)
        pair = [point(start), point(end)]
        if pair == self._shown:
            return
        body = grid_payload(pair[0], pair[1], self._method)
        if self._shown is not None and self._shown[0] == pair[0]:
            self._grid = self.emit(KIND, "revised", body, revises=self._grid).seq
        else:
            self._grid = self.emit(KIND, "detected", body).seq
        self._shown = pair

    def get_state(self) -> dict[str, Any]:
        return {
            "zigzag": self._zigzag.dump_state(),
            "swings": self._swings,
            "method": self._method,
            "grid": self._grid,
            "shown": self._shown,
        }

    def set_state(self, state: dict[str, Any]) -> None:
        self._zigzag.load_state(state["zigzag"])
        self._swings = state["swings"]
        self._method = state["method"]
        self._grid = state["grid"]
        self._shown = state["shown"]


def _ts(swing: dict[str, Any]) -> float:
    return datetime.fromisoformat(swing["timestamp"]).timestamp()
