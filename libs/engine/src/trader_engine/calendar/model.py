"""Модель торгового календаря: окна сессий, правила с датами действия, сессии."""

from dataclasses import dataclass
from datetime import date, datetime, time


@dataclass(frozen=True, slots=True)
class SessionWindow:
    """Непрерывное окно торгов в локальном времени биржи.

    Окно не пересекает полночь. ``end`` — исключающая граница.
    ``next_day`` — окно относится к следующему торговому дню (вечерняя сессия
    FORTS до 23.03.2026).
    """

    name: str
    start: time
    end: time
    next_day: bool = False

    def __post_init__(self) -> None:
        if self.start.tzinfo is not None or self.end.tzinfo is not None:
            raise ValueError("Время окна задаётся локальным, без tzinfo")
        if self.end <= self.start:
            raise ValueError(f"Окно {self.name!r}: конец должен быть позже начала")


def _validate_windows(windows: tuple[SessionWindow, ...]) -> None:
    for previous, current in zip(windows, windows[1:], strict=False):
        if current.start < previous.end:
            raise ValueError(
                f"Окна {previous.name!r} и {current.name!r} пересекаются "
                "или идут не по порядку"
            )


@dataclass(frozen=True, slots=True)
class SessionRule:
    """Расписание сессий, действующее в интервале дат (обе границы включены)."""

    effective_from: date
    effective_to: date | None
    weekday_windows: tuple[SessionWindow, ...]
    weekend_windows: tuple[SessionWindow, ...]
    # Локальное время, от которого каждый календарный день строится сетка баров.
    bar_anchor: time

    def __post_init__(self) -> None:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to раньше effective_from")
        _validate_windows(self.weekday_windows)
        _validate_windows(self.weekend_windows)

    def contains(self, day: date) -> bool:
        return self.effective_from <= day and (
            self.effective_to is None or day <= self.effective_to
        )


@dataclass(frozen=True, slots=True)
class Session:
    """Конкретное окно торгов на конкретную дату; время в UTC (aware)."""

    name: str
    start: datetime
    end: datetime
    trading_day: date
    is_weekend: bool

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment < self.end


@dataclass(frozen=True, slots=True)
class BarSlot:
    """Ячейка сетки баров, в которую попал момент времени.

    ``start`` — начало ячейки (open time бара, UTC). ``close_time`` — конец
    последнего куска сессии внутри ячейки (ADR-0004). ``is_partial`` — бар
    короче номинальной длительности: обрезан сессией, клирингом или границей
    сетки.
    """

    start: datetime
    close_time: datetime
    trading_day: date
    is_partial: bool
