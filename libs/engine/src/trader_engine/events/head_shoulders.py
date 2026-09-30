"""Head & Shoulders и Inverse Head & Shoulders (ADR-0022, spec §26).

Пять подряд идущих подтверждённых swing: плечо – впадина – голова – впадина – плечо
(для перевёрнутой фигуры наоборот). Условия: голова выступает за оба плеча не меньше,
чем на ``head_min_atr`` · ATR; плечи равны в пределах ``shoulder_tol_atr`` · ATR и
лежат за обеими точками шеи. Шея — прямая через две впадины (пики), подтверждение —
закрытие за шеей, цель — высота головы над шеей. Отмена до подтверждения — закрытие за
головой.
"""

from typing import Any

from pydantic import Field

from trader_engine.events.geometry import Line
from trader_engine.events.patterns import BEARISH, BULLISH, PatternBase, PatternParams
from trader_engine.events.registry import register
from trader_engine.indicators.base import BarInput


class HeadShouldersParams(PatternParams):
    shoulder_tol_atr: float = Field(
        default=1.0, gt=0, le=20, description="Допуск равенства плеч, ATR"
    )
    head_min_atr: float = Field(
        default=0.5,
        gt=0,
        le=20,
        description="Минимальный выступ головы над плечами, ATR",
    )


@register
class HeadShoulders(PatternBase):
    name = "head_shoulders"
    title = "Head & Shoulders, Inverse H&S"
    Params = HeadShouldersParams
    pattern_types = ("head_shoulders", "inverse_head_shoulders")

    def detect(self, swings: list[dict[str, Any]], bar: BarInput) -> None:
        if not self.atr or len(swings) < 5:
            return
        window = swings[-5:]
        kind = window[0]["type"]
        other = "low" if kind == "high" else "high"
        if [s["type"] for s in window] != [kind, other, kind, other, kind]:
            return
        p = self.typed_params(HeadShouldersParams)
        atr = self.atr
        sign = 1 if kind == "high" else -1  # +1 — H&S, −1 — перевёрнутая
        left, b1, head, b2, right = window
        shoulder_gap = abs(left["price"] - right["price"])
        shoulders = (left["price"], right["price"])
        prominence = sign * head["price"] - max(sign * s for s in shoulders)
        if shoulder_gap > p.shoulder_tol_atr * atr or prominence < p.head_min_atr * atr:
            return
        neck_top = max(sign * b1["price"], sign * b2["price"])
        if any(sign * s <= neck_top for s in shoulders):
            return
        neckline = Line(
            b1["index"], b1["price"], b2["index"], b2["price"], b1["ts"], b2["ts"]
        )
        height = sign * (head["price"] - neckline.at(head["index"]))
        roles = ["left_shoulder", "neck1", "head", "neck2", "right_shoulder"]
        arms = (head["index"] - left["index"], right["index"] - head["index"])
        self.open(
            "head_shoulders" if sign > 0 else "inverse_head_shoulders",
            BEARISH if sign > 0 else BULLISH,
            [{"role": role, **s} for role, s in zip(roles, window, strict=True)],
            neckline,
            invalid_level=head["price"],
            height=height,
            components={
                "precision": 1 - shoulder_gap / (p.shoulder_tol_atr * atr),
                "symmetry": 1 - abs(arms[0] - arms[1]) / max(sum(arms), 1),
            },
            features={
                "shoulder_gap_atr": round(shoulder_gap / atr, 4),
                "head_prominence_atr": round(prominence / atr, 4),
                "neckline_slope": neckline.slope,
            },
        )
