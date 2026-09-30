"""Вхождения для статистики: точки входа, извлечённые из лога событий (ADR-0023).

Из цепочек событий движков паттернов и уровней строятся ``Occurrence`` — момент
входа (``available_at`` события), направление и то, что нужно исходам: цель,
момент отмены. Направление и группа событий уровня задаются ролью уровня:
касание поддержки и пробой сопротивления вверх — bullish, зеркально — bearish.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

from trader_engine.indicators.base import BarInput
from trader_engine.stats.outcomes import (
    DEFAULT_HORIZONS,
    Direction,
    HorizonOutcome,
    compute_outcomes,
)

Entry = Literal["confirmed", "candidate", "touch", "break"]
GROUP_TOUCH = "level_touch"
GROUP_BREAK = "level_break"

_SUPPORT, _RESISTANCE = "support", "resistance"


class EventLike(Protocol):
    """Минимум от события: подходит и ``trader_engine.events.Event``, и строка лога."""

    @property
    def seq(self) -> int: ...

    @property
    def kind(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def payload(self) -> dict[str, Any]: ...

    @property
    def available_at(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class Occurrence:
    """Одно вхождение: вход в ``available_at`` события, направление, цель и отмена.

    ``entry`` — чем вызван вход (подтверждение паттерна, кандидат, касание, пробой);
    ``final_state`` — где цепочка закончилась (у уровня — ``None``);
    ``false_breakout`` — паттерн подтверждён, но пробой не удержался.
    """

    key: str
    engine: str
    kind: Literal["pattern", "level"]
    group: str
    direction: Direction
    entry: Entry
    available_at: datetime
    target: float | None = None
    invalidated_at: datetime | None = None
    final_state: str | None = None
    reason: str | None = None
    false_breakout: bool = False
    quality: float | None = None
    meta: dict[str, Any] = field(default_factory=dict[str, Any])


def pattern_occurrences(
    engine: str,
    events: Iterable[EventLike],
    *,
    include_candidates: bool = False,
) -> list[Occurrence]:
    """Вхождения паттернов: вход по подтверждению; кандидаты — по явному флагу.

    Кандидат (обнаружен, но не подтверждён — ещё жив или отменён до подтверждения)
    входит от момента обнаружения и без цели.
    """
    chains: dict[int, list[EventLike]] = {}
    for event in sorted(events, key=lambda e: e.seq):
        if event.kind == "pattern":
            chains.setdefault(int(event.payload["id"]), []).append(event)
    found: list[Occurrence] = []
    for pattern_id, chain in chains.items():
        confirmed = next((e for e in chain if e.status == "confirmed"), None)
        entry_event = confirmed or (chain[0] if include_candidates else None)
        if entry_event is None:
            continue
        last = chain[-1]
        cancel = last if last.status == "invalidated" else None
        first_payload = chain[0].payload
        found.append(
            Occurrence(
                key=f"{engine}:{pattern_id}",
                engine=engine,
                kind="pattern",
                group=str(first_payload["pattern"]),
                direction=_direction(first_payload["direction"]),
                entry="confirmed" if confirmed else "candidate",
                available_at=entry_event.available_at,
                target=_number(entry_event.payload.get("target"))
                if confirmed
                else None,
                invalidated_at=cancel.available_at
                if cancel and cancel.available_at > entry_event.available_at
                else None,
                final_state=str(last.payload["state"]),
                reason=cancel.payload.get("reason") if cancel else None,
                false_breakout=bool(
                    confirmed
                    and cancel
                    and cancel.payload.get("reason") == "false_breakout"
                ),
                quality=_number(last.payload["quality"]["score"]),
                meta={"points": len(first_payload["points"])},
            )
        )
    return sorted(found, key=lambda o: (o.available_at, o.key))


def level_occurrences(engine: str, events: Iterable[EventLike]) -> list[Occurrence]:
    """Касания и пробои уровней: каждое такое событие — отдельное вхождение."""
    found: list[Occurrence] = []
    for event in sorted(events, key=lambda e: e.seq):
        if event.kind != "level" or event.status != "revised":
            continue
        payload = event.payload
        if payload.get("change") == "touch":
            role, group, entry = payload["role"], GROUP_TOUCH, "touch"
            direction: Direction = "bullish" if role == _SUPPORT else "bearish"
        elif payload.get("state") == "broken":
            role, group, entry = payload["previous_role"], GROUP_BREAK, "break"
            direction = "bullish" if role == _RESISTANCE else "bearish"
        else:
            continue
        strength: dict[str, Any] = payload.get("strength") or {}
        found.append(
            Occurrence(
                key=f"{engine}:{payload['id']}:{event.seq}",
                engine=engine,
                kind="level",
                group=group,
                direction=direction,
                entry=entry,  # type: ignore[arg-type]
                available_at=event.available_at,
                quality=_number(strength.get("score")),
                meta={
                    "level_id": payload["id"],
                    "family": payload["family"],
                    "source": payload["source"],
                    "price": payload["price"],
                    "role": role,
                    "touches": payload["touches"],
                },
            )
        )
    return sorted(found, key=lambda o: (o.available_at, o.key))


def _direction(value: Any) -> Direction:
    return "bullish" if value == "bullish" else "bearish"


def _number(value: Any) -> float | None:
    return None if value is None else float(value)


def close_index(bars: Sequence[BarInput]) -> dict[datetime, int]:
    """Индекс баров по времени закрытия — для быстрого поиска точки входа."""
    return {bar.close_time: i for i, bar in enumerate(bars)}


def occurrence_outcomes(
    occurrence: Occurrence,
    bars: Sequence[BarInput],
    atrs: Sequence[float | None],
    index: Mapping[datetime, int],
    *,
    roll_times: Sequence[datetime] = (),
    horizons: Sequence[int] = DEFAULT_HORIZONS,
) -> list[HorizonOutcome] | None:
    """Исходы вхождения; ``None``, если бара входа нет в данных."""
    entry = index.get(occurrence.available_at)
    if entry is None:
        return None
    return compute_outcomes(
        bars,
        entry,
        occurrence.direction,
        atrs[entry],
        target=occurrence.target,
        invalidated_at=occurrence.invalidated_at,
        roll_times=roll_times,
        horizons=horizons,
    )
