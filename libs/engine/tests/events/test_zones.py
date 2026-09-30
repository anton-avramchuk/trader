"""Кластеризация уровней в confluence-зоны."""

import pytest

from trader_engine.events.zones import ZoneLevel, cluster_levels


def level(
    id_: int,
    price: float,
    family: str = "swing",
    score: float = 50.0,
    role: str = "resistance",
) -> ZoneLevel:
    return ZoneLevel(
        id=id_,
        price=price,
        source=f"{family}_{id_}",
        family=family,
        role=role,
        score=score,
        source_timeframe="1h",
        created_at="2026-09-28T10:00:00+00:00",
    )


def test_spec_example_forms_one_resistance_zone() -> None:
    levels = [
        level(1, 68410, "swing", 60),
        level(2, 68418, "fib", 40),
        level(3, 68422, "pivot", 70),
        level(4, 68425, "prev", 50),
        level(5, 69000, "swing", 30),
    ]

    zones = cluster_levels(levels, atr=20.0, threshold=0.5)  # порог 10 пунктов

    assert [(z.low, z.high, len(z.members)) for z in zones] == [
        (68410, 68425, 4),
        (69000, 69000, 1),
    ]
    zone = zones[0]
    assert zone.families == ("fib", "pivot", "prev", "swing")
    assert zone.strength == pytest.approx(min(100, 70 + 10 * 3))
    assert 68410 < zone.center < 68425
    assert zone.width_atr(20.0) == pytest.approx(0.75)


def test_threshold_is_measured_in_atr() -> None:
    levels = [level(1, 100), level(2, 104), level(3, 108)]

    assert len(cluster_levels(levels, atr=10.0, threshold=0.5)) == 1  # шаг 4 ≤ 5
    assert len(cluster_levels(levels, atr=5.0, threshold=0.5)) == 3  # шаг 4 > 2.5
    assert len(cluster_levels(levels, atr=5.0, threshold=1.0)) == 1


def test_zone_role_and_center_follow_the_strongest_level() -> None:
    zones = cluster_levels(
        [level(1, 100, score=10, role="support"), level(2, 101, score=90)], atr=10.0
    )

    (zone,) = zones
    assert zone.role == "resistance"
    assert zone.center == pytest.approx(100.9, abs=0.01)


def test_order_and_edges() -> None:
    assert cluster_levels([], atr=1.0) == []
    zones = cluster_levels([level(2, 30), level(1, 10)], atr=1.0)
    assert [z.low for z in zones] == [10, 30]
    with pytest.raises(ValueError):
        cluster_levels([level(1, 1)], atr=0.0)
    with pytest.raises(ValueError):
        cluster_levels([level(1, 1)], atr=1.0, threshold=0.0)


def test_strength_is_capped_at_100() -> None:
    levels = [level(i, 100 + i * 0.1, family=f"f{i}", score=95) for i in range(5)]

    (zone,) = cluster_levels(levels, atr=10.0)

    assert zone.strength == 100.0
