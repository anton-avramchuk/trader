"""PIP Engine (spec §27) и нормализация формы (ADR-0025).

Окно цен сжимается до *k* опорных точек: к концам окна итеративно добавляется точка
с наибольшим вертикальным отклонением от прямой между уже выбранными соседями.
Форма — точки (время, цена): время в долях ширины окна, цена — от первой точки,
в ATR на последнем баре окна или в процентах. Всё зависит только от данных окна.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from trader_engine.indicators.base import BarInput

DEFAULT_PIP_POINTS = 9
MIN_PIP_POINTS = 3

Normalization = Literal["atr", "percent"]
NORMALIZATIONS: tuple[Normalization, ...] = ("atr", "percent")


@dataclass(frozen=True, slots=True)
class Shape:
    """Нормализованная форма окна: ``times`` в [0, 1], ``values`` от нуля."""

    indices: tuple[int, ...]
    times: tuple[float, ...]
    values: tuple[float, ...]
    normalization: Normalization


def pip_indices(values: Sequence[float], k: int = DEFAULT_PIP_POINTS) -> list[int]:
    """Индексы *k* опорных точек по возрастанию (всё окно, если оно короче *k*)."""
    n = len(values)
    if k < MIN_PIP_POINTS:
        raise ValueError(f"k должно быть не меньше {MIN_PIP_POINTS}")
    if n <= k:
        return list(range(n))
    chosen = [0, n - 1]
    while len(chosen) < k:
        best_index = -1
        best_gap = 0.0
        for left, right in zip(chosen, chosen[1:], strict=False):
            span = right - left
            if span < 2:
                continue
            slope = (values[right] - values[left]) / span
            for i in range(left + 1, right):
                gap = abs(values[i] - (values[left] + slope * (i - left)))
                if gap > best_gap:
                    best_gap = gap
                    best_index = i
        if best_index < 0:
            break
        chosen.append(best_index)
        chosen.sort()
    return chosen


def normalize(
    prices: Sequence[float],
    indices: Sequence[int],
    mode: Normalization,
    atr: float | None,
) -> Shape | None:
    """Форма по выбранным точкам; ``None``, если нормализацию посчитать нельзя."""
    if len(indices) < 2:
        return None
    first = prices[indices[0]]
    if mode == "atr":
        if atr is None or atr <= 0:
            return None
        values = tuple((prices[i] - first) / atr for i in indices)
    elif mode == "percent":
        if first == 0:
            return None
        values = tuple((prices[i] / first - 1.0) * 100.0 for i in indices)
    else:
        raise ValueError(f"неизвестная нормализация: {mode}")
    width = indices[-1] - indices[0]
    if width <= 0:
        return None
    times = tuple((i - indices[0]) / width for i in indices)
    return Shape(tuple(indices), times, values, mode)


def window_shape(
    bars: Sequence[BarInput],
    atr: float | None,
    k: int = DEFAULT_PIP_POINTS,
    mode: Normalization = "atr",
) -> Shape | None:
    """Форма окна баров по close; ``atr`` — ATR на последнем баре окна."""
    if len(bars) < MIN_PIP_POINTS:
        return None
    prices = [bar.close for bar in bars]
    return normalize(prices, pip_indices(prices, k), mode, atr)
