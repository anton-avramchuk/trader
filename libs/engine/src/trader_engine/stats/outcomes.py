"""Forward outcomes события (ADR-0023): доходность, MFE и MAE на горизонтах.

Чистые функции над барами. Вход — close бара, на котором событие стало известно
(``available_at``); окно горизонта ``h`` — следующие ``h`` баров. Будущее за
горизонтом не читается, поэтому исход события не зависит от данных дальше окна.
"""

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from trader_engine.events.atr import WilderAtr
from trader_engine.indicators.base import BarInput

DEFAULT_HORIZONS: tuple[int, ...] = (5, 10, 20, 50)
DEFAULT_ATR_PERIOD = 14

Direction = Literal["bullish", "bearish"]
FirstHit = Literal["target", "invalidated"]


@dataclass(frozen=True, slots=True)
class HorizonOutcome:
    """Исход на одном горизонте; при ``censored`` числовые поля — ``None``.

    ``mfe_*`` и ``mae_*`` неотрицательны: благоприятная и неблагоприятная
    экскурсия от входа в направлении события. ``ret_*`` — со знаком.
    """

    horizon: int
    censored: bool
    ret_atr: float | None
    ret_pct: float | None
    mfe_atr: float | None
    mfe_pct: float | None
    mae_atr: float | None
    mae_pct: float | None
    first_hit: FirstHit | None
    ambiguous_bar: bool
    crosses_session_gap: bool
    crosses_weekend: bool
    crosses_roll: bool


def atr_series(
    bars: Sequence[BarInput], period: int = DEFAULT_ATR_PERIOD
) -> list[float | None]:
    """ATR Уайлдера на закрытии каждого бара (``None`` до прогрева)."""
    atr = WilderAtr(period)
    return [atr.update(bar) for bar in bars]


def entry_index(bars: Sequence[BarInput], available_at: datetime) -> int | None:
    """Индекс бара, закрывшегося в ``available_at`` (``None``, если такого нет)."""
    closes = [bar.close_time for bar in bars]
    index = bisect_left(closes, available_at)
    if index < len(bars) and closes[index] == available_at:
        return index
    return None


def _crossings(
    window: Sequence[BarInput], roll_times: Sequence[datetime]
) -> tuple[bool, bool, bool]:
    """Есть ли на пути от входа (``window[0]``) пауза, выходные или ролл."""
    session = weekend = False
    for previous, current in zip(window, window[1:], strict=False):
        if current.timestamp > previous.close_time:
            session = True
        if (
            previous.trading_day.isocalendar()[:2]
            != current.trading_day.isocalendar()[:2]
        ):
            weekend = True
    start, end = window[0].close_time, window[-1].close_time
    roll = any(start < roll_time <= end for roll_time in roll_times)
    return session, weekend, roll


def compute_outcomes(
    bars: Sequence[BarInput],
    entry: int,
    direction: Direction,
    atr: float | None,
    *,
    target: float | None = None,
    invalidated_at: datetime | None = None,
    roll_times: Sequence[datetime] = (),
    horizons: Sequence[int] = DEFAULT_HORIZONS,
) -> list[HorizonOutcome]:
    """Исходы события на каждом горизонте.

    ``atr`` — ATR на входе (``None`` или 0: единицы ATR не определены). ``target`` —
    цена цели паттерна; ``invalidated_at`` — ``close_time`` бара отмены. Если цель и
    отмена в одном баре, первой считается отмена и ставится ``ambiguous_bar``
    (пессимистично, ADR-0013).
    """
    sign = 1.0 if direction == "bullish" else -1.0
    price = bars[entry].close
    scale = atr if atr is not None and atr > 0 else None
    if invalidated_at is not None and invalidated_at <= bars[entry].close_time:
        invalidated_at = None  # отмена до входа к исходу не относится
    outcomes: list[HorizonOutcome] = []
    for horizon in horizons:
        end = entry + horizon
        if end >= len(bars):
            outcomes.append(
                HorizonOutcome(
                    horizon,
                    True,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    False,
                    False,
                    False,
                    False,
                )
            )
            continue
        window = bars[entry : end + 1]
        future = window[1:]
        move = sign * (window[-1].close - price)
        if direction == "bullish":
            favorable = max(bar.high for bar in future) - price
            adverse = price - min(bar.low for bar in future)
        else:
            favorable = price - min(bar.low for bar in future)
            adverse = max(bar.high for bar in future) - price
        favorable, adverse = max(favorable, 0.0), max(adverse, 0.0)
        first_hit, ambiguous = _first_hit(future, direction, target, invalidated_at)
        session, weekend, roll = _crossings(window, roll_times)
        outcomes.append(
            HorizonOutcome(
                horizon=horizon,
                censored=False,
                ret_atr=None if scale is None else move / scale,
                ret_pct=move / price * 100,
                mfe_atr=None if scale is None else favorable / scale,
                mfe_pct=favorable / price * 100,
                mae_atr=None if scale is None else adverse / scale,
                mae_pct=adverse / price * 100,
                first_hit=first_hit,
                ambiguous_bar=ambiguous,
                crosses_session_gap=session,
                crosses_weekend=weekend,
                crosses_roll=roll,
            )
        )
    return outcomes


def _first_hit(
    future: Sequence[BarInput],
    direction: Direction,
    target: float | None,
    invalidated_at: datetime | None,
) -> tuple[FirstHit | None, bool]:
    """Что произошло раньше в окне: цель или отмена; оба в одном баре — отмена."""
    for bar in future:
        invalidated = invalidated_at is not None and bar.close_time >= invalidated_at
        reached = target is not None and (
            bar.high >= target if direction == "bullish" else bar.low <= target
        )
        if invalidated and reached:
            return "invalidated", True
        if invalidated:
            return "invalidated", False
        if reached:
            return "target", False
    return None, False
