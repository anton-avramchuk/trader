"""Baseline «случайная точка в том же режиме» (ADR-0023).

Для каждого события берутся случайные бары того же инструмента и TF, у которых тот
же режим (тренд × волатильность) и которые лежат вне окон реальных событий. Выбор
детерминирован при фиксированном ``seed``. Направление baseline-точки — направление
события, для которого она взята, поэтому исходы сравнимы один к одному.
"""

import random
from collections.abc import Sequence
from dataclasses import dataclass

from trader_engine.stats.outcomes import Direction
from trader_engine.stats.regime import Regime

DEFAULT_PER_EVENT = 5


@dataclass(frozen=True, slots=True)
class BaselineRequest:
    """Реальное событие: индекс бара входа и направление."""

    entry: int
    direction: Direction


@dataclass(frozen=True, slots=True)
class BaselinePoint:
    index: int
    direction: Direction
    regime: Regime
    for_entry: int


def sample_baseline(
    regimes: Sequence[Regime | None],
    requests: Sequence[BaselineRequest],
    *,
    exclusion: int,
    per_event: int = DEFAULT_PER_EVENT,
    seed: int = 0,
) -> list[BaselinePoint]:
    """До ``per_event`` точек на событие; события без известного режима пропускаются.

    Каждый бар берётся не более одного раза на режим и направление: повторы одних и
    тех же баров сузили бы доверительный интервал без новой информации. Если пул
    режима исчерпан, последним событиям точек достаётся меньше.

    ``exclusion`` — радиус в барах вокруг входов реальных событий (обычно наибольший
    горизонт), где baseline-точек быть не должно: окна исходов не должны
    пересекаться с событиями.
    """
    blocked = [False] * len(regimes)
    for request in requests:
        low = max(request.entry - exclusion, 0)
        high = min(request.entry + exclusion, len(regimes) - 1)
        for index in range(low, high + 1):
            blocked[index] = True
    pools: dict[Regime, list[int]] = {}
    for index, regime in enumerate(regimes):
        if regime is not None and not blocked[index]:
            pools.setdefault(regime, []).append(index)
    rng = random.Random(seed)
    queues: dict[tuple[Regime, str], list[int]] = {}
    points: list[BaselinePoint] = []
    for request in sorted(requests, key=lambda r: (r.entry, r.direction)):
        regime = regimes[request.entry] if 0 <= request.entry < len(regimes) else None
        if regime is None:
            continue
        queue = queues.get((regime, request.direction))
        if queue is None:
            queue = list(pools.get(regime, []))
            rng.shuffle(queue)
            queues[(regime, request.direction)] = queue
        for _ in range(min(per_event, len(queue))):
            points.append(
                BaselinePoint(queue.pop(), request.direction, regime, request.entry)
            )
    return points
