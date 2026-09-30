"""Range breakout: выход из горизонтального диапазона (ADR-0022, spec §26).

Диапазон — окно из 4–``max_swings`` последних подтверждённых swing, где максимумы лежат
в пределах ``tol_atr`` · ATR от общей верхней границы (среднее), минимумы — от нижней,
касаний не меньше двух с каждой стороны, ширина не меньше ``min_height_atr`` · ATR и
не у́же ``min_bars`` баров. Пробой — закрытие за границей на ``break_atr`` · ATR: вверх
(``bullish``) или вниз (``bearish``). Пока диапазон не пробит, открыты оба вхождения;
пробой одной границы отменяет (``broken``) вхождение, ждущее пробоя другой. Цель —
ширина диапазона от пробитой границы.
"""

from typing import Any

from pydantic import Field

from trader_engine.events.geometry import Line
from trader_engine.events.patterns import BEARISH, BULLISH, PatternBase, PatternParams
from trader_engine.events.registry import register
from trader_engine.indicators.base import BarInput

MIN_SWINGS = 4


class RangeParams(PatternParams):
    break_atr: float = Field(
        default=0.25, ge=0, le=10, description="Закрытие за границей не меньше, ATR"
    )
    min_bars: int = Field(default=6, ge=2, description="Минимальная ширина, баров")
    max_swings: int = Field(default=8, ge=MIN_SWINGS, le=16, description="Окно, swing")


@register
class RangeBreakout(PatternBase):
    name = "range_breakout"
    title = "Range breakout"
    Params = RangeParams
    pattern_types = ("range_breakout",)

    def detect(self, swings: list[dict[str, Any]], bar: BarInput) -> None:
        if not self.atr:
            return
        p = self.typed_params(RangeParams)
        for size in range(min(p.max_swings, len(swings)), MIN_SWINGS - 1, -1):
            if self._try(swings[-size:], p):
                return  # побеждает самое длинное окно

    def _try(self, window: list[dict[str, Any]], p: RangeParams) -> bool:
        atr = self.atr or 0.0
        highs = [s["price"] for s in window if s["type"] == "high"]
        lows = [s["price"] for s in window if s["type"] == "low"]
        first, last = window[0], window[-1]
        if (
            len(highs) < 2
            or len(lows) < 2
            or last["index"] - first["index"] < p.min_bars
        ):
            return False
        top, bottom = sum(highs) / len(highs), sum(lows) / len(lows)
        tol = p.tol_atr * atr
        deviation = max(
            max(abs(h - top) for h in highs), max(abs(v - bottom) for v in lows)
        )
        if deviation > tol or top - bottom < p.min_height_atr * atr:
            return False
        buffer = p.break_atr * atr
        upper = Line(
            first["index"],
            top + buffer,
            last["index"],
            top + buffer,
            first["ts"],
            last["ts"],
        )
        lower = Line(
            first["index"],
            bottom - buffer,
            last["index"],
            bottom - buffer,
            first["ts"],
            last["ts"],
        )
        points: list[dict[str, Any]] = []
        counts = {"high": 0, "low": 0}
        for swing in window:
            counts[swing["type"]] += 1
            points.append({"role": f"{swing['type']}{counts[swing['type']]}", **swing})
        indexes = {s["index"] for s in window}
        opened = False
        for direction, boundary, opposite in (
            (BULLISH, upper, lower),
            (BEARISH, lower, upper),
        ):
            if any(
                occ["direction"] == direction
                and occ["state"] == "candidate"
                and occ["pattern"] == "range_breakout"
                and len(indexes & {pt["index"] for pt in occ["points"]}) >= 2
                for occ in self._live
            ):
                continue
            occurrence = self.open(
                "range_breakout",
                direction,
                points,
                boundary,
                invalid_level=opposite,
                height=top - bottom,
                components={
                    "precision": 1 - deviation / tol if tol else 1.0,
                    "symmetry": min(len(highs), len(lows)) / max(len(highs), len(lows)),
                },
                features={
                    "range_high": top,
                    "range_low": bottom,
                    "touches_upper": len(highs),
                    "touches_lower": len(lows),
                },
            )
            opened = opened or occurrence is not None
        return opened
