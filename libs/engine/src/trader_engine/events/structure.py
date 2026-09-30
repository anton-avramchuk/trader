"""Market structure: HH/HL/LH/LL и состояние тренда по подтверждённым swing (spec §16).

Движок строится поверх ZigZag (те же параметры) и использует только его
подтверждённые точки, поэтому знает о них не раньше закрытия бара подтверждения.
События:

- ``structure_point`` — подтверждённая точка получила метку относительно
  предыдущей точки того же типа (``HH``/``LH`` для максимумов, ``HL``/``LL`` для
  минимумов); первая точка каждого типа метки не имеет и события не даёт;
- ``trend_state`` — смена состояния: ``uptrend`` (последние HH и HL), ``downtrend``
  (последние LH и LL), иначе ``range``. Пока нет меток обоих типов, состояния нет.

BOS/CHoCH в MVP не входят.
"""

from typing import Any

from pydantic import BaseModel

from trader_engine.events.base import EventEngine
from trader_engine.events.registry import register
from trader_engine.events.swing import HIGH, ZigZag, ZigZagParams
from trader_engine.indicators.base import BarInput

POINT_KIND = "structure_point"
STATE_KIND = "trend_state"
LABELS = {
    (HIGH, True): "HH",
    (HIGH, False): "LH",
    ("low", True): "HL",
    ("low", False): "LL",
}


def trend_of(high_label: str | None, low_label: str | None) -> str | None:
    if high_label is None or low_label is None:
        return None
    if (high_label, low_label) == ("HH", "HL"):
        return "uptrend"
    if (high_label, low_label) == ("LH", "LL"):
        return "downtrend"
    return "range"


@register
class MarketStructure(EventEngine):
    name = "market_structure"
    title = "Market structure (HH/HL/LH/LL, тренд)"
    Params = ZigZagParams

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        self._zigzag = ZigZag(self.params)
        self._last: dict[str, float | None] = {HIGH: None, "low": None}
        self._labels: dict[str, str | None] = {HIGH: None, "low": None}
        self._trend: str | None = None

    def on_bar(self, bar: BarInput) -> None:
        for event in self._zigzag.update(bar):
            if event.status == "confirmed":
                self._on_swing(event.payload)

    def _on_swing(self, swing: dict[str, Any]) -> None:
        kind, price = swing["type"], swing["price"]
        previous = self._last[kind]
        self._last[kind] = price
        if previous is None:
            return
        higher = price > previous
        label = LABELS[kind, higher]
        self._labels[kind] = label
        self.emit(
            POINT_KIND,
            "confirmed",
            {
                "label": label,
                "type": kind,
                "price": price,
                "previous_price": previous,
                "timestamp": swing["timestamp"],
                "method": swing["method"],
            },
        )
        state = trend_of(self._labels[HIGH], self._labels["low"])
        if state is not None and state != self._trend:
            self.emit(
                STATE_KIND,
                "confirmed",
                {"state": state, "previous": self._trend, "trigger": label},
            )
            self._trend = state

    def get_state(self) -> dict[str, Any]:
        return {
            "zigzag": self._zigzag.dump_state(),
            "last": self._last,
            "labels": self._labels,
            "trend": self._trend,
        }

    def set_state(self, state: dict[str, Any]) -> None:
        self._zigzag.load_state(state["zigzag"])
        self._last = state["last"]
        self._labels = state["labels"]
        self._trend = state["trend"]
