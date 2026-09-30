"""Выборка событий «as-of t» и разрешение цепочек ревизий (ADR-0003, ADR-0021)."""

import hashlib
from collections.abc import Iterable, Sequence
from datetime import datetime

from trader_engine.events.base import Event
from trader_engine.indicators.base import BarInput


def known_at(events: Iterable[Event], as_of: datetime) -> list[Event]:
    """События, доступные к моменту ``as_of`` (``available_at <= as_of``)."""
    return [event for event in events if event.available_at <= as_of]


def current_events(
    events: Sequence[Event], as_of: datetime | None = None
) -> list[Event]:
    """Актуальная картина к ``as_of``: последняя версия каждой цепочки.

    Цепочка — исходное событие и все его ревизии (``revises``). Отменённые
    (``invalidated``) цепочки исключаются. Порядок — по началу цепочки.
    """
    visible = list(events) if as_of is None else known_at(events, as_of)
    root_of: dict[int, int] = {}
    latest: dict[int, Event] = {}
    for event in sorted(visible, key=lambda item: item.seq):
        root = event.seq if event.revises is None else root_of[event.revises]
        root_of[event.seq] = root
        latest[root] = event
    return [
        latest[root] for root in sorted(latest) if latest[root].status != "invalidated"
    ]


def _step(previous: str, bar: BarInput) -> str:
    row = (
        f"{previous}|{bar.timestamp.isoformat()}|{bar.close_time.isoformat()}|"
        f"{bar.open!r}|{bar.high!r}|{bar.low!r}|{bar.close!r}|{bar.volume!r}"
    )
    return hashlib.sha256(row.encode()).hexdigest()


def fingerprint(bars: Iterable[BarInput], seed: str = "") -> str:
    """Цепочный отпечаток баров: меняется, если изменился любой бар или порядок."""
    digest = seed
    for bar in bars:
        digest = _step(digest, bar)
    return digest
