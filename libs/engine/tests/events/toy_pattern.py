"""Учебный детектор для тестов каркаса паттернов: двойная вершина из трёх swing."""

from typing import Any

from trader_engine.events import Line, PatternBase, PatternParams
from trader_engine.indicators import BarInput


class ToyDouble(PatternBase):
    name = "toy_double"
    title = "Учебная двойная вершина"
    Params = PatternParams
    pattern_types = ("double_top",)

    def detect(self, swings: list[dict[str, Any]], bar: BarInput) -> None:
        if len(swings) < 3:
            return
        a, b, c = swings[-3:]
        if (a["type"], b["type"], c["type"]) != ("high", "low", "high"):
            return
        tol = self.typed_params(PatternParams).tol_atr * (self.atr or 0.0)
        gap = abs(a["price"] - c["price"])
        if gap > tol:
            return
        top = max(a["price"], c["price"])
        left, right = b["index"] - a["index"], c["index"] - b["index"]
        self.open(
            "double_top",
            "bearish",
            [
                {"role": "top1", **a},
                {"role": "valley", **b},
                {"role": "top2", **c},
            ],
            Line(b["index"], b["price"], c["index"], b["price"]),
            invalid_level=top,
            height=(a["price"] + c["price"]) / 2 - b["price"],
            components={
                "precision": 1 - gap / tol if tol else 1.0,
                "symmetry": 1 - abs(left - right) / max(left + right, 1),
            },
        )
