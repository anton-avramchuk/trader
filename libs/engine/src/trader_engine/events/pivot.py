"""Pivot Engine: дневные и недельные уровни (classic, fibonacci, woodie, camarilla).

Периоды — торговые день (``trading_day`` бара) и неделя (ISO-неделя торгового дня),
поэтому вечерняя сессия и выходные попадают в правильный период. Период считается
завершённым, когда приходит бар следующего периода: событие ``pivot`` выпускается на
его закрытии (это ``available_at``) и относится к новому периоду (``payload.applies_to``
— его первый торговый день). В payload — предыдущие High/Low/Close, PP, R1–R3, S1–S3.
"""

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from trader_engine.events.base import EventEngine
from trader_engine.events.registry import register
from trader_engine.indicators.base import BarInput

KIND = "pivot"
Formula = Literal["classic", "fibonacci", "woodie", "camarilla"]


class PivotParams(BaseModel):
    formula: Formula = Field(default="classic", description="Формула уровней")
    daily: bool = Field(default=True, description="Дневные уровни")
    weekly: bool = Field(default=True, description="Недельные уровни")


def levels(formula: str, high: float, low: float, close: float) -> dict[str, float]:
    """PP, R1–R3, S1–S3 по High/Low/Close завершённого периода."""
    rng = high - low
    pp = (high + low + close) / 3
    if formula == "classic":
        return {
            "pp": pp,
            "r1": 2 * pp - low,
            "r2": pp + rng,
            "r3": high + 2 * (pp - low),
            "s1": 2 * pp - high,
            "s2": pp - rng,
            "s3": low - 2 * (high - pp),
        }
    if formula == "fibonacci":
        return {
            "pp": pp,
            "r1": pp + 0.382 * rng,
            "r2": pp + 0.618 * rng,
            "r3": pp + rng,
            "s1": pp - 0.382 * rng,
            "s2": pp - 0.618 * rng,
            "s3": pp - rng,
        }
    if formula == "woodie":
        pp = (high + low + 2 * close) / 4
        return {
            "pp": pp,
            "r1": 2 * pp - low,
            "r2": pp + rng,
            "r3": high + 2 * (pp - low),
            "s1": 2 * pp - high,
            "s2": pp - rng,
            "s3": low - 2 * (high - pp),
        }
    if formula == "camarilla":
        step = 1.1 * rng
        return {
            "pp": pp,
            "r1": close + step / 12,
            "r2": close + step / 6,
            "r3": close + step / 4,
            "s1": close - step / 12,
            "s2": close - step / 6,
            "s3": close - step / 4,
        }
    raise ValueError(f"Неизвестная формула {formula!r}")


def period_key(period: str, day: date) -> str:
    if period == "day":
        return day.isoformat()
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


@register
class Pivot(EventEngine):
    name = "pivot"
    title = "Pivot: дневные и недельные уровни"
    Params = PivotParams

    def __init__(self, params: BaseModel | None = None) -> None:
        super().__init__(params)
        # период → {key, day (первый торговый день), high, low, close}
        self._current: dict[str, dict[str, Any]] = {}

    def _periods(self) -> list[str]:
        p = self.typed_params(PivotParams)
        return [n for n, on in (("day", p.daily), ("week", p.weekly)) if on]

    def on_bar(self, bar: BarInput) -> None:
        formula = self.typed_params(PivotParams).formula
        for period in self._periods():
            key = period_key(period, bar.trading_day)
            current = self._current.get(period)
            if current is not None and current["key"] == key:
                current["high"] = max(current["high"], bar.high)
                current["low"] = min(current["low"], bar.low)
                current["close"] = bar.close
                continue
            if current is not None:
                self.emit(
                    KIND,
                    "confirmed",
                    {
                        "period": period,
                        "formula": formula,
                        "applies_to": bar.trading_day.isoformat(),
                        "source_period": current["key"],
                        "high": current["high"],
                        "low": current["low"],
                        "close": current["close"],
                        **levels(
                            formula, current["high"], current["low"], current["close"]
                        ),
                    },
                )
            self._current[period] = {
                "key": key,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
            }

    def get_state(self) -> dict[str, Any]:
        return {"current": self._current}

    def set_state(self, state: dict[str, Any]) -> None:
        self._current = state["current"]
