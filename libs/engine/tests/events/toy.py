"""Учебный движок для тестов каркаса: «кандидат в максимум» с ревизиями.

Новый максимум закрытия — событие ``detected``; следующий максимум пересматривает
его (``revised``); если за ``confirm_after`` баров максимум не обновлён — ``confirmed``;
падение ниже ``floor`` после подтверждения отменяет цепочку (``invalidated``).
"""

from typing import Any

from pydantic import BaseModel, Field

from trader_engine.events import EventEngine
from trader_engine.indicators import BarInput


class ToyParams(BaseModel):
    confirm_after: int = Field(default=3, ge=1)
    floor: float = Field(default=0.9, gt=0, le=1)


class ToyHighs(EventEngine):
    name = "toy_highs"
    title = "Учебный движок"
    Params = ToyParams

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        self.best: float | None = None
        self.last_seq: int | None = None
        self.age = 0
        self.confirmed = False

    def on_bar(self, bar: BarInput) -> None:
        p = self.typed_params(ToyParams)
        if self.best is None or bar.close > self.best:
            revises = self.last_seq
            status = "detected" if revises is None else "revised"
            event = self.emit("high", status, {"price": bar.close}, revises=revises)
            self.best, self.last_seq, self.age, self.confirmed = (
                bar.close,
                event.seq,
                0,
                False,
            )
            return
        self.age += 1
        if not self.confirmed and self.age == p.confirm_after:
            self.emit("high", "confirmed", {"price": self.best}, revises=self.last_seq)
            self.confirmed = True
            # Дальнейшие ревизии продолжают цепочку от подтверждённого события.
            self.last_seq = self._next_seq - 1
        elif self.confirmed and self.last_seq is not None:
            if bar.close < self.best * p.floor:
                self.emit("high", "invalidated", {}, revises=self.last_seq)
                self.best, self.last_seq, self.confirmed = None, None, False

    def get_state(self) -> dict[str, Any]:
        return {
            "best": self.best,
            "last_seq": self.last_seq,
            "age": self.age,
            "confirmed": self.confirmed,
        }

    def set_state(self, state: dict[str, Any]) -> None:
        self.best = state["best"]
        self.last_seq = state["last_seq"]
        self.age = state["age"]
        self.confirmed = state["confirmed"]
