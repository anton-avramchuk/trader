"""Triangle, Wedge и Channel по двум трендовым линиям (ADR-0022, spec §26).

Окно из последних 4–``max_swings`` подтверждённых swing: верхняя линия — МНК по
максимумам, нижняя — по минимумам (минимум два касания каждой; отклонение точек от
линии не больше ``tol_atr`` · ATR). По наклонам и сходимости фигура классифицируется:

- сходятся: восходящий/нисходящий/симметричный треугольник, восходящий/нисходящий клин;
- параллельны: канал (восходящий, нисходящий, горизонтальный).

Подтверждение — закрытие за границей, цель — ширина фигуры в её начале от пробитой
границы. Направления: восходящий треугольник и нисходящий клин — вверх, нисходящий
треугольник и восходящий клин — вниз; симметричный треугольник и каналы дают два
вхождения (пробой вверх и вниз), из которых подтверждается не больше одного: пробой
одной границы отменяет вхождение, ждущее пробоя другой. Отмена до подтверждения —
закрытие за противоположной границей. Более ранняя формация того же типа и
направления, которую продолжает окно, новое вхождение не открывает.
"""

from typing import Any

from pydantic import Field

from trader_engine.events.geometry import Line, fit_line, max_deviation
from trader_engine.events.patterns import BEARISH, BULLISH, PatternBase, PatternParams
from trader_engine.events.registry import register
from trader_engine.indicators.base import BarInput

MIN_SWINGS = 4

# фигура → направления пробоя (вверх — верхняя граница, вниз — нижняя)
DIRECTIONS = {
    "triangle_ascending": (BULLISH,),
    "triangle_descending": (BEARISH,),
    "triangle_symmetric": (BULLISH, BEARISH),
    "wedge_rising": (BEARISH,),
    "wedge_falling": (BULLISH,),
    "channel_ascending": (BULLISH, BEARISH),
    "channel_descending": (BULLISH, BEARISH),
    "channel_horizontal": (BULLISH, BEARISH),
}


class TrendlineParams(PatternParams):
    flat_atr: float = Field(
        default=0.5,
        gt=0,
        le=20,
        description="Линия горизонтальна, если её ход за окно не больше, ATR",
    )
    converge_ratio: float = Field(
        default=0.75,
        gt=0,
        lt=1,
        description="Фигура сходится: ширина в конце ≤ доля ширины в начале",
    )
    parallel_ratio: float = Field(
        default=0.2,
        gt=0,
        lt=1,
        description="Линии параллельны: ширина меняется не больше, чем на долю",
    )
    min_bars: int = Field(default=6, ge=2, description="Минимальная ширина, баров")
    max_swings: int = Field(default=6, ge=MIN_SWINGS, le=12, description="Окно, swing")


def classify(
    upper: Line, lower: Line, first: int, last: int, atr: float, p: TrendlineParams
) -> str | None:
    """Тип фигуры по двум линиям на окне ``first..last`` (``None`` — не фигура)."""
    start = upper.at(first) - lower.at(first)
    end = upper.at(last) - lower.at(last)
    if start <= 0 or end <= 0:
        return None  # линии пересеклись внутри окна
    span = last - first
    up, low = upper.slope * span, lower.slope * span
    eps = p.flat_atr * atr
    flat_up, flat_low = abs(up) <= eps, abs(low) <= eps
    if end <= p.converge_ratio * start:
        if flat_up and low > eps:
            return "triangle_ascending"
        if flat_low and up < -eps:
            return "triangle_descending"
        if up < -eps and low > eps:
            return "triangle_symmetric"
        if up > eps and low > eps:
            return "wedge_rising"
        if up < -eps and low < -eps:
            return "wedge_falling"
        return None
    if abs(end - start) <= p.parallel_ratio * start:
        if flat_up and flat_low:
            return "channel_horizontal"
        if up > eps and low > eps:
            return "channel_ascending"
        if up < -eps and low < -eps:
            return "channel_descending"
    return None


@register
class Trendlines(PatternBase):
    name = "trendlines"
    title = "Triangle, Wedge, Channel"
    Params = TrendlineParams
    pattern_types = tuple(DIRECTIONS)

    def detect(self, swings: list[dict[str, Any]], bar: BarInput) -> None:
        if not self.atr:
            return
        p = self.typed_params(TrendlineParams)
        for size in range(min(p.max_swings, len(swings)), MIN_SWINGS - 1, -1):
            if self._try(swings[-size:], p):
                return  # побеждает самое длинное окно

    def _try(self, window: list[dict[str, Any]], p: TrendlineParams) -> bool:
        atr = self.atr or 0.0
        highs = [(s["index"], s["price"]) for s in window if s["type"] == "high"]
        lows = [(s["index"], s["price"]) for s in window if s["type"] == "low"]
        first, last = window[0]["index"], window[-1]["index"]
        distinct = len({i for i, _ in highs}) > 1 and len({i for i, _ in lows}) > 1
        if not distinct or last - first < p.min_bars:
            return False
        upper, lower = fit_line(highs), fit_line(lows)
        tol = p.tol_atr * atr
        deviation = max(max_deviation(upper, highs), max_deviation(lower, lows))
        if deviation > tol:
            return False
        shape = classify(upper, lower, first, last, atr, p)
        if shape is None:
            return False
        height = upper.at(first) - lower.at(first)
        balance = min(len(highs), len(lows)) / max(len(highs), len(lows))
        points: list[dict[str, Any]] = []
        counts = {"high": 0, "low": 0}
        for swing in window:
            counts[swing["type"]] += 1
            points.append({"role": f"{swing['type']}{counts[swing['type']]}", **swing})
        opened = False
        for direction in DIRECTIONS[shape]:
            if self._continues(shape, direction, window):
                continue
            boundary, opposite = (
                (upper, lower) if direction == BULLISH else (lower, upper)
            )
            occurrence = self.open(
                shape,
                direction,
                points,
                _with_times(boundary, window),
                invalid_level=opposite,
                height=height,
                components={
                    "precision": 1 - deviation / tol if tol else 1.0,
                    "symmetry": balance,
                },
                features={
                    "upper_slope_atr": round(upper.slope / atr, 4),
                    "lower_slope_atr": round(lower.slope / atr, 4),
                    "touches_upper": len(highs),
                    "touches_lower": len(lows),
                },
            )
            opened = opened or occurrence is not None
        return opened

    def _continues(
        self, shape: str, direction: str, window: list[dict[str, Any]]
    ) -> bool:
        """Есть ли ждущее вхождение той же фигуры, которое это окно продолжает."""
        indexes = {s["index"] for s in window}
        return any(
            occ["pattern"] == shape
            and occ["direction"] == direction
            and occ["state"] == "candidate"
            and len(indexes & {pt["index"] for pt in occ["points"]}) >= 2
            for occ in self._live
        )


def _with_times(line: Line, window: list[dict[str, Any]]) -> Line:
    """Линия, опирающаяся на концы окна (с их временем)."""
    first, last = window[0], window[-1]
    return Line(
        first["index"],
        line.at(first["index"]),
        last["index"],
        line.at(last["index"]),
        first["ts"],
        last["ts"],
    )
