"""Контракт движка событий (ADR-0003, ADR-0021).

Движок — каузальная инкрементальная машина: ``update(bar)`` принимает очередной бар
и возвращает новые события. События неизменяемы; пересмотр и отмена — новые
события со ссылкой ``revises`` на предыдущее событие цепочки. ``available_at``
выставляет базовый класс (= закрытие бара, на котором событие стало известно), так
что движок не может выдать событие «из будущего».
"""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel

from trader_engine.indicators.base import BarInput

EventStatus = Literal["detected", "confirmed", "revised", "invalidated"]
REVISING: frozenset[str] = frozenset({"revised", "invalidated"})


@dataclass(frozen=True, slots=True)
class Event:
    """Событие движка; ``seq`` — порядковый номер в прогоне (0, 1, 2 …).

    ``revises`` — ``seq`` предыдущего события той же цепочки. ``detected_at`` —
    когда движок впервые заметил объект, ``confirmed_at`` — когда подтвердил,
    ``available_at`` — с какого момента событие можно использовать.
    """

    seq: int
    kind: str
    status: EventStatus
    payload: dict[str, Any]
    detected_at: datetime
    confirmed_at: datetime | None
    available_at: datetime
    revises: int | None = None


class EventEngine(ABC):
    """Базовый класс движка (подкласс: ``on_bar``, ``get_state``, ``set_state``).

    Состояние должно целиком описываться JSON-словарём: по нему прогон продолжается
    на новых барах без пересчёта истории (результат совпадает с прогоном «с нуля»).
    """

    name: ClassVar[str]
    title: ClassVar[str]
    # Версия алгоритма: смена → новый прогон, старые результаты не трогаются.
    version: ClassVar[int] = 1
    Params: ClassVar[type[BaseModel]]

    def __init__(self, params: BaseModel | None = None) -> None:
        self.params = params if params is not None else self.Params()
        self._next_seq = 0
        self._bar: BarInput | None = None
        self._last_close: datetime | None = None
        self._pending: list[Event] = []

    @abstractmethod
    def on_bar(self, bar: BarInput) -> None:
        """Обработать бар; события выпускаются через ``emit``."""

    @abstractmethod
    def get_state(self) -> dict[str, Any]:
        """Состояние движка в виде JSON-словаря (без служебных счётчиков)."""

    @abstractmethod
    def set_state(self, state: dict[str, Any]) -> None:
        """Восстановить состояние, полученное из ``get_state``."""

    def typed_params[P: BaseModel](self, model: type[P]) -> P:
        assert isinstance(self.params, model)
        return self.params

    def update(self, bar: BarInput) -> list[Event]:
        """Принять бар (строго после предыдущего) и вернуть выпущенные события."""
        if self._last_close is not None and bar.close_time <= self._last_close:
            raise ValueError(
                f"{self.name}: бар {bar.close_time.isoformat()} не позже предыдущего"
            )
        self._bar = bar
        self._pending = []
        self.on_bar(bar)
        self._last_close = bar.close_time
        pending, self._pending = self._pending, []
        return pending

    def emit(
        self,
        kind: str,
        status: EventStatus,
        payload: dict[str, Any],
        *,
        detected_at: datetime | None = None,
        confirmed_at: datetime | None = None,
        revises: int | None = None,
    ) -> Event:
        """Выпустить событие на текущем баре (вызывается только из ``on_bar``)."""
        bar = self._bar
        if bar is None:
            raise RuntimeError("emit вне on_bar")
        now = bar.close_time
        detected = detected_at if detected_at is not None else now
        if status == "confirmed" and confirmed_at is None:
            confirmed_at = now
        if (status in REVISING) != (revises is not None) and status != "confirmed":
            raise ValueError(f"{self.name}: {status} и revises=None несовместимы")
        if revises is not None and not 0 <= revises < self._next_seq:
            raise ValueError(f"{self.name}: revises={revises} — нет такого события")
        if detected > now or (confirmed_at is not None and confirmed_at > now):
            raise ValueError(f"{self.name}: событие из будущего относительно бара")
        if confirmed_at is not None and confirmed_at < detected:
            raise ValueError(f"{self.name}: confirmed_at раньше detected_at")
        try:
            json.dumps(payload)
        except TypeError as error:
            raise ValueError(f"{self.name}: payload не сериализуется в JSON") from error
        event = Event(
            seq=self._next_seq,
            kind=kind,
            status=status,
            payload=payload,
            detected_at=detected,
            confirmed_at=confirmed_at,
            available_at=now,
            revises=revises,
        )
        self._next_seq += 1
        self._pending.append(event)
        return event

    def dump_state(self) -> dict[str, Any]:
        """Полное состояние прогона для продолжения (счётчик, последний бар, движок)."""
        return {
            "next_seq": self._next_seq,
            "last_close": None
            if self._last_close is None
            else self._last_close.isoformat(),
            "engine": self.get_state(),
        }

    def load_state(self, state: dict[str, Any]) -> None:
        self._next_seq = int(state["next_seq"])
        last = state["last_close"]
        self._last_close = None if last is None else datetime.fromisoformat(last)
        self.set_state(state["engine"])


def run_engine(engine: EventEngine, bars: list[BarInput]) -> list[Event]:
    """Прогнать движок по барам с нуля (или продолжить с текущего состояния)."""
    events: list[Event] = []
    for bar in bars:
        events.extend(engine.update(bar))
    return events
