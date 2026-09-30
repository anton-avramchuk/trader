"""Levels Engine: уровни поддержки/сопротивления и их жизненный цикл (ADR-0012).

Источники (семейства): ``swing`` — подтверждённые swing high/low ZigZag; ``pivot`` —
PP, R1–R3, S1–S3 дневные и недельные; ``prev`` — предыдущие день/неделя
High/Low/Close; ``fib`` — уровни сетки завершённой ноги (swing → swing). Горизонтальные
исторические уровни — это сохраняемые swing-уровни, отдельного источника у них нет.

Уровень — цепочка событий ``level``: ``detected`` (появился), ``revised`` (касание;
пробой — смена роли, состояние ``broken``), ``invalidated`` (``expired`` по возрасту
или ``superseded`` новым уровнем того же источника). Касание: заход в зону
±``touch_k``·ATR и отбой на ``touch_m``·ATR от крайней точки без закрытия за зоной;
пробой — закрытие за зоной. Сила — вектор компонентов и агрегат v1
(``docs/levels-strength-v1.md``). Пока ATR не прогрет, состояния уровней не меняются.
"""

from typing import Any

from pydantic import BaseModel, Field

from trader_engine.events.atr import WilderAtr
from trader_engine.events.base import EventEngine, EventStatus
from trader_engine.events.fibonacci import fib_levels
from trader_engine.events.pivot import Formula, Pivot, PivotParams
from trader_engine.events.registry import register
from trader_engine.events.swing import ZigZag, ZigZagParams
from trader_engine.indicators.base import BarInput

KIND = "level"
RESISTANCE, SUPPORT = "resistance", "support"

# Формула силы v1: версионируемый конфиг. Изменение весов — новая версия.
STRENGTH_V1: dict[str, Any] = {
    "version": 1,
    "weights": {
        "touches": 0.30,
        "source": 0.25,
        "rejection": 0.20,
        "confluence": 0.15,
        "age": 0.10,
    },
    # значение компонента, при котором вклад достигает максимума
    "caps": {"touches": 4, "rejection": 3.0, "confluence": 3, "age": 200},
    "source_weight": {
        "swing": 0.6,
        "pivot_day": 0.6,
        "pivot_week": 0.8,
        "prev_day": 0.7,
        "prev_week": 0.9,
        "fib": 0.4,
    },
    "confluence_atr": 0.5,
}
PIVOT_NAMES = ("pp", "r1", "r2", "r3", "s1", "s2", "s3")
PREVIOUS = ("high", "low", "close")


class LevelsParams(ZigZagParams):
    touch_k: float = Field(default=0.25, gt=0, le=5, description="Зона ± k·ATR")
    touch_m: float = Field(default=0.5, gt=0, le=10, description="Отбой ≥ m·ATR")
    max_age: int = Field(default=1000, ge=1, description="Возраст жизни, баров")
    pivot_formula: Formula = Field(default="classic", description="Формула pivot")
    swing: bool = True
    pivot: bool = True
    fibonacci: bool = True


def strength(level: dict[str, Any], others: int) -> dict[str, Any]:
    """Компоненты и агрегат v1 (0–100) для уровня; ``others`` — соседние семейства."""
    cfg = STRENGTH_V1
    touches = level["touches"]
    components = {
        "touches": touches,
        "source": cfg["source_weight"][level["source_type"]],
        "rejection": level["rebound_sum"] / touches if touches else 0.0,
        "confluence": others,
        "age": level["age"],
    }
    score = 0.0
    for name, weight in cfg["weights"].items():
        cap = cfg["caps"].get(name, 1.0)
        score += weight * min(components[name] / cap, 1.0)
    return {
        "version": cfg["version"],
        "components": components,
        "score": round(100 * score, 2),
    }


@register
class Levels(EventEngine):
    name = "levels"
    title = "Levels: уровни S/R, касания, пробои, сила"
    Params = LevelsParams

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        p = self.typed_params(LevelsParams)
        self._zigzag = ZigZag(p)
        self._pivot = Pivot(PivotParams(formula=p.pivot_formula))
        self._atr = WilderAtr(p.atr_period)
        self._levels: list[dict[str, Any]] = []
        self._next_id = 1
        self._swings: list[float] = []  # две последние подтверждённые точки

    # --- жизненный цикл -------------------------------------------------

    def on_bar(self, bar: BarInput) -> None:
        atr = self._atr.update(bar)
        if atr is not None:
            for level in list(self._levels):
                self._advance(level, bar, atr)
        for event in self._zigzag.update(bar):
            if event.status == "confirmed":
                self._on_swing(event.payload, bar, atr)
        for event in self._pivot.update(bar):
            self._on_pivot(event.payload, bar, atr)

    def _advance(self, level: dict[str, Any], bar: BarInput, atr: float) -> None:
        p = self.typed_params(LevelsParams)
        price, role = level["price"], level["role"]
        zone = p.touch_k * atr
        level["age"] += 1
        beyond = (
            bar.close > price + zone if role == RESISTANCE else bar.close < price - zone
        )
        if beyond:
            flipped = SUPPORT if role == RESISTANCE else RESISTANCE
            self._emit(
                level,
                "revised",
                bar,
                atr,
                state="broken",
                role=flipped,
                previous_role=role,
            )
            self._levels.remove(level)
            return
        if level["age"] > p.max_age:
            self._emit(
                level, "invalidated", bar, atr, state="expired", reason="expired"
            )
            self._levels.remove(level)
            return
        overlaps = bar.high >= price - zone and bar.low <= price + zone
        if not overlaps:
            level["armed"] = True
        if not (level["in_touch"] or (level["armed"] and overlaps)):
            return
        edge = bar.high if role == RESISTANCE else bar.low
        if not level["in_touch"]:
            level["in_touch"], level["extreme"] = True, edge
        elif role == RESISTANCE:
            level["extreme"] = max(level["extreme"], edge)
        else:
            level["extreme"] = min(level["extreme"], edge)
        rebound = (
            level["extreme"] - bar.close
            if role == RESISTANCE
            else bar.close - level["extreme"]
        )
        if rebound >= p.touch_m * atr:
            level["touches"] += 1
            level["rebound_sum"] += rebound / atr
            level["in_touch"], level["armed"] = False, False
            self._emit(level, "revised", bar, atr, state="active", change="touch")

    # --- источники ------------------------------------------------------

    def _on_swing(
        self, swing: dict[str, Any], bar: BarInput, atr: float | None
    ) -> None:
        p = self.typed_params(LevelsParams)
        price = swing["price"]
        if p.swing:
            self._add(f"swing_{swing['type']}", "swing", "swing", price, bar, atr)
        self._swings = [*self._swings[-1:], price]
        if p.fibonacci and len(self._swings) == 2:
            grid = fib_levels(self._swings[0], self._swings[1])
            for key, value in grid["retracement"].items():
                self._add(f"fib_ret_{key}", "fib", "fib", value, bar, atr)
            for key, value in grid["extension"].items():
                if key != "100":
                    self._add(f"fib_ext_{key}", "fib", "fib", value, bar, atr)

    def _on_pivot(
        self, payload: dict[str, Any], bar: BarInput, atr: float | None
    ) -> None:
        if not self.typed_params(LevelsParams).pivot:
            return
        period = payload["period"]
        for name in PIVOT_NAMES:
            self._add(
                f"pivot_{period}_{name}",
                "pivot",
                f"pivot_{period}",
                payload[name],
                bar,
                atr,
            )
        for name in PREVIOUS:
            self._add(
                f"prev_{period}_{name}",
                "prev",
                f"prev_{period}",
                payload[name],
                bar,
                atr,
            )

    def _same_source(self, source: str) -> list[dict[str, Any]]:
        return [x for x in self._levels if x["source"] == source]

    def _add(
        self,
        source: str,
        family: str,
        source_type: str,
        price: float,
        bar: BarInput,
        atr: float | None,
    ) -> None:
        replaced = [] if family == "swing" else self._same_source(source)
        for old in replaced:
            self._emit(
                old, "invalidated", bar, atr, state="superseded", reason="superseded"
            )
            self._levels.remove(old)
        level: dict[str, Any] = {
            "id": self._next_id,
            "source": source,
            "family": family,
            "source_type": source_type,
            "price": price,
            "role": RESISTANCE if price > bar.close else SUPPORT,
            "created_at": bar.close_time.isoformat(),
            "age": 0,
            "touches": 0,
            "rebound_sum": 0.0,
            "in_touch": False,
            "extreme": None,
            "armed": False,
            "chain": None,
        }
        self._next_id += 1
        self._levels.append(level)
        self._emit(level, "detected", bar, atr, state="active")

    # --- события --------------------------------------------------------

    def _emit(
        self,
        level: dict[str, Any],
        status: EventStatus,
        bar: BarInput,
        atr: float | None,
        **extra: Any,
    ) -> None:
        others = 0
        if atr is not None:
            near = STRENGTH_V1["confluence_atr"] * atr
            others = len(
                {
                    x["family"]
                    for x in self._levels
                    if x is not level
                    and x["family"] != level["family"]
                    and abs(x["price"] - level["price"]) <= near
                }
            )
        payload = {
            "id": level["id"],
            "source": level["source"],
            "family": level["family"],
            "price": level["price"],
            "role": level["role"],
            "state": "active",
            "touches": level["touches"],
            "created_at": level["created_at"],
            "strength": strength(level, others),
        } | extra
        event = self.emit(KIND, status, payload, revises=level["chain"])
        level["chain"] = event.seq
        if "role" in extra:
            level["role"] = extra["role"]

    # --- состояние ------------------------------------------------------

    def get_state(self) -> dict[str, Any]:
        return {
            "zigzag": self._zigzag.dump_state(),
            "pivot": self._pivot.dump_state(),
            "atr": self._atr.dump(),
            "levels": self._levels,
            "next_id": self._next_id,
            "swings": self._swings,
        }

    def set_state(self, state: dict[str, Any]) -> None:
        self._zigzag.load_state(state["zigzag"])
        self._pivot.load_state(state["pivot"])
        self._atr.load(state["atr"])
        self._levels = state["levels"]
        self._next_id = state["next_id"]
        self._swings = state["swings"]
