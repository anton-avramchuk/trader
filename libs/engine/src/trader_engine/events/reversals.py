"""Double/Triple Top и Bottom (ADR-0022, spec §26).

Вершины (впадины) — подтверждённые swing ZigZag, равные в пределах ``tol_atr`` · ATR.
Шея — линия через промежуточные впадины (пики): у двойной вершины горизонталь через
впадину, у тройной — прямая через две впадины. Подтверждение — закрытие за шеей, цель —
высота паттерна от шеи. Вершины дают медвежий паттерн, впадины — бычий. Отмена до
подтверждения: закрытие дальше крайней вершины (впадины) на ``tol_atr`` · ATR.
"""

from typing import Any

from trader_engine.events.geometry import Line
from trader_engine.events.patterns import BEARISH, BULLISH, PatternBase, PatternParams
from trader_engine.events.registry import register
from trader_engine.indicators.base import BarInput

# тип крайней точки → (имя паттерна, роль вершины, роль впадины, направление, знак)
TOPS = ("top", "valley", BEARISH, 1)
BOTTOMS = ("bottom", "peak", BULLISH, -1)
SHAPES = {"high": TOPS, "low": BOTTOMS}


def _symmetry(intervals: list[int]) -> float:
    half = len(intervals) // 2
    left, right = sum(intervals[:half]), sum(intervals[half:])
    return 1 - abs(left - right) / max(left + right, 1)


@register
class DoubleTriple(PatternBase):
    name = "double_triple"
    title = "Double/Triple Top и Bottom"
    Params = PatternParams
    pattern_types = ("double_top", "double_bottom", "triple_top", "triple_bottom")

    def detect(self, swings: list[dict[str, Any]], bar: BarInput) -> None:
        if not self.atr:
            return
        kind = swings[-1]["type"]
        for count in (2, 3):
            self._try(swings, kind, count)

    def _try(self, swings: list[dict[str, Any]], kind: str, count: int) -> None:
        size = 2 * count - 1
        if len(swings) < size:
            return
        window = swings[-size:]
        other = "low" if kind == "high" else "high"
        if [s["type"] for s in window] != [kind, other] * (count - 1) + [kind]:
            return
        atr = self.atr or 0.0
        tol = self.typed_params(PatternParams).tol_atr * atr
        extremes, between = window[0::2], window[1::2]
        prices = [e["price"] for e in extremes]
        gap = max(prices) - min(prices)
        if gap > tol:
            return
        top_role, mid_role, direction, sign = SHAPES[kind]
        name = f"{'double' if count == 2 else 'triple'}_{top_role}"
        roles = [
            f"{top_role}{i // 2 + 1}"
            if i % 2 == 0
            else mid_role + (str(i // 2 + 1) if count == 3 else "")
            for i in range(size)
        ]
        first, last = between[0], between[-1]
        if count == 2:  # шея двойной вершины — горизонталь через впадину
            line = Line(
                first["index"],
                first["price"],
                extremes[-1]["index"],
                first["price"],
                first["ts"],
                extremes[-1]["ts"],
            )
        else:
            line = Line(
                first["index"],
                first["price"],
                last["index"],
                last["price"],
                first["ts"],
                last["ts"],
            )
        depth = sum(b["price"] for b in between) / len(between)
        height = sign * (sum(prices) / len(prices) - depth)
        intervals = [
            window[i + 1]["index"] - window[i]["index"] for i in range(size - 1)
        ]
        self.open(
            name,
            direction,
            [{"role": role, **s} for role, s in zip(roles, window, strict=True)],
            line,
            invalid_level=(max(prices) if sign > 0 else min(prices)) + sign * tol,
            height=height,
            components={
                "precision": 1 - gap / tol if tol else 1.0,
                "symmetry": _symmetry(intervals),
            },
            features={"peak_gap_atr": round(gap / atr, 4)},
        )
