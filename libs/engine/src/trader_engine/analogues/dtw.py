"""DTW-ядро (spec §28, ADR-0025): расстояние между нормализованными формами.

Классический DTW по паре (время, цена) с полосой Сакоэ–Чибы. Накопленная
стоимость делится на длину пути выравнивания, поэтому расстояния форм с разным
числом точек сравнимы. Чистый Python: формы — 8–15 точек, матрица крошечная.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from trader_engine.analogues.pip import Shape

DEFAULT_BAND = 0.2
DEFAULT_TIME_WEIGHT = 1.0


@dataclass(frozen=True, slots=True)
class DtwResult:
    """Результат сопоставления: ``path`` — пары индексов точек (запрос, кандидат)."""

    distance: float
    normalized_distance: float
    similarity: float
    path: tuple[tuple[int, int], ...]


def similarity(normalized_distance: float) -> float:
    """Мера похожести в (0, 1]: 1 — формы совпали."""
    return 1.0 / (1.0 + normalized_distance)


def dtw(
    a: Shape,
    b: Shape,
    band: float = DEFAULT_BAND,
    time_weight: float = DEFAULT_TIME_WEIGHT,
) -> DtwResult | None:
    """DTW двух форм одной нормализации; ``None`` — путь в полосе не найден.

    ``band`` — доля длины (полуширина полосы Сакоэ–Чибы), но не меньше разницы
    длин, иначе до угла матрицы не дойти. Расстояние симметрично по аргументам.
    """
    if a.normalization != b.normalization:
        raise ValueError("формы с разной нормализацией несравнимы")
    if not 0.0 <= band <= 1.0:
        raise ValueError("band должна быть в [0, 1]")
    n, m = len(a.values), len(b.values)
    if n == 0 or m == 0:
        return None
    radius = max(math.ceil(band * max(n, m)), abs(n - m))
    inf = math.inf
    cost = [[inf] * m for _ in range(n)]
    for i in range(n):
        for j in range(max(0, i - radius), min(m, i + radius + 1)):
            step = math.hypot(
                time_weight * (a.times[i] - b.times[j]), a.values[i] - b.values[j]
            )
            if i == 0 and j == 0:
                best = 0.0
            else:
                best = min(
                    cost[i - 1][j - 1] if i and j else inf,
                    cost[i - 1][j] if i else inf,
                    cost[i][j - 1] if j else inf,
                )
            cost[i][j] = step + best
    total = cost[n - 1][m - 1]
    if math.isinf(total):
        return None
    path = _backtrack(cost, n, m)
    normalized = total / len(path)
    return DtwResult(total, normalized, similarity(normalized), path)


def _backtrack(
    cost: Sequence[Sequence[float]], n: int, m: int
) -> tuple[tuple[int, int], ...]:
    i, j = n - 1, m - 1
    path = [(i, j)]
    while i or j:
        options: list[tuple[float, int, int]] = []
        if i and j:
            options.append((cost[i - 1][j - 1], i - 1, j - 1))
        if i:
            options.append((cost[i - 1][j], i - 1, j))
        if j:
            options.append((cost[i][j - 1], i, j - 1))
        _, i, j = min(options, key=lambda option: option[0])
        path.append((i, j))
    path.reverse()
    return tuple(path)
