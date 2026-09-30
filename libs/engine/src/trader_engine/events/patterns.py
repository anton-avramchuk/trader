"""Каркас паттернов (ADR-0022): детектор поверх подтверждённых swing.

Базовый класс ведёт ZigZag и ATR, хранит недавние подтверждённые точки и
жизненный цикл вхождений; детектор реализует только ``detect``: из списка точек
собирает геометрию и вызывает ``open``. Событие ``pattern`` — цепочка на вхождение:

- ``detected`` — геометрия сформирована (последняя точка подтверждена swing'ом);
- ``confirmed`` — закрытие бара за линией шеи/границы (``confirmed_at = close_time``);
- ``invalidated`` — закрытие за экстремумом паттерна до подтверждения (``broken``),
  истёк ``max_bars`` (``expired``) либо возврат за линию в течение ``false_break_bars``
  после подтверждения (``false_breakout``).

Допуски — в ATR того же таймфрейма. Пока ATR не прогрет, паттерны не открываются.
Качество — вектор компонентов и агрегат v1 (``docs/pattern-quality-v1.md``).
"""

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from trader_engine.events.atr import WilderAtr
from trader_engine.events.base import EventEngine, EventStatus
from trader_engine.events.geometry import Line
from trader_engine.events.swing import ZigZag, ZigZagParams
from trader_engine.indicators.base import BarInput

KIND = "pattern"
BULLISH, BEARISH = "bullish", "bearish"
MAX_SWINGS = 16

# Формула качества v1: версионируемый конфиг; изменение весов — новая версия.
QUALITY_V1: dict[str, Any] = {
    "version": 1,
    "weights": {"precision": 0.35, "symmetry": 0.25, "height": 0.25, "duration": 0.15},
    # значение, при котором компонент достигает максимума
    "caps": {"height_atr": 6.0, "width_bars": 30.0},
}


class PatternParams(ZigZagParams):
    """Общие параметры детекторов (к параметрам ZigZag добавляются допуски и сроки)."""

    tol_atr: float = Field(default=0.5, gt=0, le=10, description="Допуск, ATR")
    min_height_atr: float = Field(
        default=1.0, gt=0, le=50, description="Минимальная высота паттерна, ATR"
    )
    max_bars: int = Field(
        default=200, ge=1, description="Жизнь кандидата после последней точки, баров"
    )
    false_break_bars: int = Field(
        default=3, ge=0, description="Окно ложного пробоя после подтверждения, баров"
    )
    max_lookback: int = Field(
        default=1500, ge=10, description="Сколько баров истории помнит детектор"
    )


def quality(
    components: dict[str, float], height_atr: float, width_bars: float
) -> dict[str, Any]:
    """Агрегат v1 (0–100); ``precision`` и ``symmetry`` (0…1) даёт детектор."""
    cfg = QUALITY_V1
    parts = {
        "precision": min(max(components.get("precision", 0.0), 0.0), 1.0),
        "symmetry": min(max(components.get("symmetry", 0.0), 0.0), 1.0),
        "height": min(height_atr / cfg["caps"]["height_atr"], 1.0),
        "duration": min(width_bars / cfg["caps"]["width_bars"], 1.0),
    }
    score = 100 * sum(w * parts[name] for name, w in cfg["weights"].items())
    return {
        "version": cfg["version"],
        "components": {k: round(v, 4) for k, v in parts.items()},
        "score": round(score, 2),
    }


class PatternBase(EventEngine):
    """Базовый детектор: подкласс задаёт ``name``/``title`` и реализует ``detect``."""

    Params = PatternParams
    pattern_types: ClassVar[tuple[str, ...]] = ()

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        p = self.typed_params(PatternParams)
        self._zigzag = ZigZag(p)
        self._atr_machine = WilderAtr(p.atr_period)
        self._index = -1
        self._times: dict[str, int] = {}  # открытие бара (ISO) → номер бара
        self._swings: list[dict[str, Any]] = []
        self._live: list[dict[str, Any]] = []
        self._next_id = 1

    # --- для детекторов ---------------------------------------------------

    @property
    def atr(self) -> float | None:
        return self._atr_machine.value

    @property
    def bar_index(self) -> int:
        return self._index

    def detect(self, swings: list[dict[str, Any]], bar: BarInput) -> None:
        """Вызывается на каждой новой подтверждённой точке (и при ATR ≠ ``None``).

        ``swings`` — последние точки по времени: ``{type, price, ts, index}``.
        Детектор при совпадении геометрии вызывает ``open``.
        """
        raise NotImplementedError

    def extra_state(self) -> dict[str, Any]:
        """Собственное состояние детектора (JSON); по умолчанию нет."""
        return {}

    def load_extra_state(self, state: dict[str, Any]) -> None:
        return None

    def open(
        self,
        pattern: str,
        direction: str,
        points: list[dict[str, Any]],
        line: Line,
        invalid_level: float,
        height: float,
        components: dict[str, float],
        features: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Открыть вхождение (событие ``detected``); ``None`` — отклонено или дубликат.

        ``line`` — шея/граница пробоя; ``invalid_level`` — цена, закрытие за которой до
        подтверждения отменяет паттерн; ``height`` — высота паттерна (для цели).
        """
        p = self.typed_params(PatternParams)
        atr = self.atr
        if atr is None or atr <= 0 or height < p.min_height_atr * atr:
            return None
        key = [pattern, *[pt["index"] for pt in points]]
        if any(occ["key"] == key for occ in self._live):
            return None
        first, last = points[0], points[-1]
        width = last["index"] - first["index"]
        occurrence: dict[str, Any] = {
            "id": self._next_id,
            "key": key,
            "pattern": pattern,
            "direction": direction,
            "points": [
                {k: pt[k] for k in ("role", "price", "ts", "index")} for pt in points
            ],
            "line": line.to_dict(),
            "invalid_level": invalid_level,
            "height": height,
            "last_index": last["index"],
            "state": "candidate",
            "confirmed_index": None,
            "target": None,
            "chain": None,
            "features": {
                "height_atr": round(height / atr, 4),
                "width_bars": width,
                "atr": atr,
                **(features or {}),
            },
            "quality": quality(components, height / atr, width),
        }
        self._next_id += 1
        self._live.append(occurrence)
        self._emit(occurrence, "detected")
        return occurrence

    # --- жизненный цикл ---------------------------------------------------

    def on_bar(self, bar: BarInput) -> None:
        p = self.typed_params(PatternParams)
        self._index += 1
        self._times[bar.timestamp.isoformat()] = self._index
        while len(self._times) > p.max_lookback:
            del self._times[next(iter(self._times))]
        self._atr_machine.update(bar)
        for occurrence in list(self._live):
            self._advance(occurrence, bar)
        for event in self._zigzag.update(bar):
            if event.status != "confirmed":
                continue
            payload = event.payload
            index = self._times.get(payload["timestamp"])
            if index is None:
                continue  # экстремум глубже, чем помнит детектор
            self._swings = [
                *self._swings[-(MAX_SWINGS - 1) :],
                {
                    "type": payload["type"],
                    "price": payload["price"],
                    "ts": payload["timestamp"],
                    "index": index,
                },
            ]
            if self.atr is not None:
                self.detect(self._swings, bar)

    def _advance(self, occ: dict[str, Any], bar: BarInput) -> None:
        p = self.typed_params(PatternParams)
        bullish = occ["direction"] == BULLISH
        level = Line.from_dict(occ["line"]).at(self._index)
        beyond = bar.close > level if bullish else bar.close < level
        if occ["state"] == "candidate":
            if beyond:
                occ["state"] = "confirmed"
                occ["confirmed_index"] = self._index
                shift = occ["height"] if bullish else -occ["height"]
                occ["target"] = level + shift
                self._emit(occ, "confirmed", breakout_level=level)
                return
            invalid = occ["invalid_level"]
            if (bar.close < invalid) if bullish else (bar.close > invalid):
                self._close(occ, "broken")
            elif self._index - occ["last_index"] > p.max_bars:
                self._close(occ, "expired")
            return
        if bar.close < level if bullish else bar.close > level:
            self._close(occ, "false_breakout")
        elif self._index - occ["confirmed_index"] >= p.false_break_bars:
            self._live.remove(occ)  # окно ложного пробоя прошло — вхождение завершено

    def _close(self, occ: dict[str, Any], reason: str) -> None:
        self._emit(occ, "invalidated", reason=reason)
        self._live.remove(occ)

    def _emit(self, occ: dict[str, Any], status: EventStatus, **extra: Any) -> None:
        points = occ["points"]
        payload: dict[str, Any] = {
            "id": occ["id"],
            "pattern": occ["pattern"],
            "direction": occ["direction"],
            "state": {
                "detected": "candidate",
                "confirmed": "confirmed",
                "invalidated": "invalidated",
            }[status],
            "start": points[0]["ts"],
            "end": points[-1]["ts"],
            "points": points,
            "line": occ["line"],
            "height": occ["height"],
            "target": occ["target"],
            "features": occ["features"],
            "quality": occ["quality"],
        } | extra
        event = self.emit(KIND, status, payload, revises=occ["chain"])
        occ["chain"] = event.seq

    # --- состояние --------------------------------------------------------

    def get_state(self) -> dict[str, Any]:
        return {
            "zigzag": self._zigzag.dump_state(),
            "atr": self._atr_machine.dump(),
            "index": self._index,
            "times": self._times,
            "swings": self._swings,
            "live": self._live,
            "next_id": self._next_id,
            "extra": self.extra_state(),
        }

    def set_state(self, state: dict[str, Any]) -> None:
        self._zigzag.load_state(state["zigzag"])
        self._atr_machine.load(state["atr"])
        self._index = state["index"]
        self._times = state["times"]
        self._swings = state["swings"]
        self._live = state["live"]
        self._next_id = state["next_id"]
        self.load_extra_state(state["extra"])
