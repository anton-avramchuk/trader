"""Fibonacci Engine: автоматическая сетка на source TF (ADR-0012, spec §23).

Сетка строится по ноге «последний подтверждённый swing → экстремум текущего колена»
(текущий кандидат ZigZag). Пока колено растёт, сетка пересматривается (``revised``);
подтверждение нового swing — новая сетка (``detected``, без ``revises``): одна цепочка
на ногу. Уровни: retracement 23.6/38.2/50/61.8/78.6 и extension 100/127.2/161.8.
"""

from typing import Any

from pydantic import BaseModel

from trader_engine.events.base import EventEngine
from trader_engine.events.registry import register
from trader_engine.events.swing import ZigZag, ZigZagParams
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


def point(payload: dict[str, Any]) -> dict[str, Any]:
    return {"price": payload["price"], "timestamp": payload["timestamp"]}


@register
class Fibonacci(EventEngine):
    name = "fibonacci"
    title = "Fibonacci: автоматическая сетка"
    Params = ZigZagParams

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        self._zigzag = ZigZag(self.params)
        self._start: dict[str, Any] | None = None  # последний подтверждённый swing
        self._end: dict[str, Any] | None = None  # экстремум текущего колена
        self._method = ""
        self._grid: int | None = None  # последнее событие цепочки сетки
        self._shown: list[dict[str, Any]] | None = (
            None  # (start, end) в последнем событии
        )

    def on_bar(self, bar: BarInput) -> None:
        for event in self._zigzag.update(bar):
            self._method = event.payload["method"]
            if event.status == "confirmed":
                self._start, self._end = point(event.payload), None
            else:
                self._end = point(event.payload)
        if self._start is None or self._end is None:
            return
        pair = [self._start, self._end]
        if pair == self._shown:
            return
        payload = grid_payload(self._start, self._end, self._method)
        new_leg = self._shown is None or self._shown[0] != self._start
        if new_leg:
            self._grid = self.emit(KIND, "detected", payload).seq
        else:
            self._grid = self.emit(KIND, "revised", payload, revises=self._grid).seq
        self._shown = pair

    def get_state(self) -> dict[str, Any]:
        return {
            "zigzag": self._zigzag.dump_state(),
            "start": self._start,
            "end": self._end,
            "method": self._method,
            "grid": self._grid,
            "shown": self._shown,
        }

    def set_state(self, state: dict[str, Any]) -> None:
        self._zigzag.load_state(state["zigzag"])
        self._start = state["start"]
        self._end = state["end"]
        self._method = state["method"]
        self._grid = state["grid"]
        self._shown = state["shown"]
