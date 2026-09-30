"""Геометрия паттернов: линии по номерам баров (ось X — порядковый номер бара).

Номера баров, а не время, чтобы ночные перерывы и выходные не искажали наклон.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Line:
    """Прямая через точки ``(i1, p1)`` и ``(i2, p2)``; ``t1``/``t2`` — время баров."""

    i1: float
    p1: float
    i2: float
    p2: float
    t1: str | None = None
    t2: str | None = None

    @property
    def slope(self) -> float:
        """Наклон: цена на бар (0 для вертикальной или вырожденной линии)."""
        return 0.0 if self.i2 == self.i1 else (self.p2 - self.p1) / (self.i2 - self.i1)

    def at(self, index: float) -> float:
        """Цена линии на баре ``index`` (в том числе за пределами отрезка)."""
        return self.p1 + self.slope * (index - self.i1)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Line":
        return Line(**data)


def fit_line(points: Sequence[tuple[float, float]]) -> Line:
    """Прямая по методу наименьших квадратов через точки ``(индекс, цена)``.

    Нужно хотя бы две точки с разными индексами; концы прямой — на крайних индексах.
    """
    if len(points) < 2:
        raise ValueError("Для линии нужно не меньше двух точек")
    xs = [x for x, _ in points]
    if max(xs) == min(xs):
        raise ValueError("Точки линии должны различаться по индексу")
    n = len(points)
    mean_x = sum(xs) / n
    mean_y = sum(y for _, y in points) / n
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / sum(
        (x - mean_x) ** 2 for x in xs
    )
    first, last = min(xs), max(xs)
    base = mean_y + slope * (first - mean_x)
    return Line(first, base, last, base + slope * (last - first))


def max_deviation(line: Line, points: Sequence[tuple[float, float]]) -> float:
    """Наибольшее отклонение точек от линии по цене (0 для пустого набора)."""
    return max((abs(price - line.at(index)) for index, price in points), default=0.0)
