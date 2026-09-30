"""Кластеризация уровней в confluence-зоны — представление в контексте графика.

Зоны не хранятся: их строят на лету из активных уровней (события ``level``) и ATR
таймфрейма графика (spec §21, ADR-0012). Соседние по цене уровни, между которыми
не больше ``threshold`` · ATR, объединяются в зону; расстояния нормируются по
волатильности, поэтому один порог работает на любой цене и любом TF.
"""

from dataclasses import dataclass

from trader_engine.events.base import Event

STRENGTH_ZONE_VERSION = 1
# Бонус к силе зоны за каждое независимое семейство источников сверх первого.
FAMILY_BONUS = 10.0


@dataclass(frozen=True, slots=True)
class ZoneLevel:
    """Активный уровень для кластеризации."""

    id: int
    price: float
    source: str
    family: str
    role: str
    score: float
    source_timeframe: str
    created_at: str


@dataclass(frozen=True, slots=True)
class Zone:
    low: float
    high: float
    center: float  # среднее цен, взвешенное силой членов
    role: str  # роль сильнейшего члена
    strength: float
    families: tuple[str, ...]
    members: tuple[ZoneLevel, ...]

    def width_atr(self, atr: float) -> float:
        return (self.high - self.low) / atr


def active_levels(events: list[Event], source_timeframe: str) -> list[ZoneLevel]:
    """Уровни, актуальные по ``current_events``: не отменены и не пробиты."""
    return [
        ZoneLevel(
            id=e.payload["id"],
            price=e.payload["price"],
            source=e.payload["source"],
            family=e.payload["family"],
            role=e.payload["role"],
            score=e.payload["strength"]["score"],
            source_timeframe=source_timeframe,
            created_at=e.payload["created_at"],
        )
        for e in events
        if e.kind == "level" and e.payload["state"] == "active"
    ]


def _zone(members: list[ZoneLevel]) -> Zone:
    weights = [m.score + 1e-9 for m in members]  # нулевая сила не обнуляет вес
    center = sum(w * m.price for w, m in zip(weights, members, strict=True)) / sum(
        weights
    )
    low, high = min(m.price for m in members), max(m.price for m in members)
    center = min(max(center, low), high)  # погрешность float не выводит за границы
    strongest = max(members, key=lambda m: (m.score, -m.price))
    families = tuple(sorted({m.family for m in members}))
    strength = min(100.0, strongest.score + FAMILY_BONUS * (len(families) - 1))
    return Zone(
        low=low,
        high=high,
        center=center,
        role=strongest.role,
        strength=round(strength, 2),
        families=families,
        members=tuple(members),
    )


def cluster_levels(
    levels: list[ZoneLevel], atr: float, threshold: float = 0.5
) -> list[Zone]:
    """Зоны по возрастанию цены; ``atr`` — ATR таймфрейма графика (> 0)."""
    if atr <= 0 or threshold <= 0:
        raise ValueError("atr и threshold должны быть положительными")
    ordered = sorted(levels, key=lambda m: (m.price, m.id))
    groups: list[list[ZoneLevel]] = []
    for level in ordered:
        if groups and level.price - groups[-1][-1].price <= threshold * atr:
            groups[-1].append(level)
        else:
            groups.append([level])
    return [_zone(group) for group in groups]
