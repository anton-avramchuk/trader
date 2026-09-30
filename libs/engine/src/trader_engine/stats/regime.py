"""Режим рынка (ADR-0023): тренд × волатильность на момент каждого бара.

Тренд — последнее состояние ``trend_state`` движка ``market_structure``, известное
на закрытии бара (``available_at``). Волатильность — терциль ATR/цена относительно
всей истории до этого бара включительно (расширяющееся окно). Режим бара зависит
только от данных до него, поэтому годится и для режима события, и для baseline.
"""

from bisect import bisect_left, insort
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from trader_engine.indicators.base import BarInput
from trader_engine.stats.occurrences import EventLike

TREND_STATES: tuple[str, ...] = ("uptrend", "downtrend", "range")
VOLATILITY_LEVELS: tuple[str, ...] = ("low", "mid", "high")
MIN_HISTORY = 50
STATE_KIND = "trend_state"


@dataclass(frozen=True, slots=True)
class Regime:
    trend: str
    volatility: str

    @property
    def label(self) -> str:
        return f"{self.trend}/{self.volatility}"


def trend_series(
    bars: Sequence[BarInput], trend_events: Iterable[EventLike]
) -> list[str | None]:
    """Состояние тренда на закрытии каждого бара (``None`` до первого состояния)."""
    changes = sorted(
        (e for e in trend_events if e.kind == STATE_KIND),
        key=lambda e: (e.available_at, e.seq),
    )
    result: list[str | None] = []
    current: str | None = None
    pointer = 0
    for bar in bars:
        while (
            pointer < len(changes) and changes[pointer].available_at <= bar.close_time
        ):
            current = str(changes[pointer].payload["state"])
            pointer += 1
        result.append(current)
    return result


def volatility_series(
    bars: Sequence[BarInput],
    atrs: Sequence[float | None],
    min_history: int = MIN_HISTORY,
) -> list[str | None]:
    """Терциль ATR/цена среди значений до бара включительно; ``None`` — мало истории."""
    past: list[float] = []
    result: list[str | None] = []
    for bar, atr in zip(bars, atrs, strict=True):
        if atr is None or bar.close <= 0:
            result.append(None)
            continue
        value = atr / bar.close
        insort(past, value)
        if len(past) < min_history:
            result.append(None)
            continue
        share = bisect_left(past, value) / len(past)
        result.append(
            VOLATILITY_LEVELS[0]
            if share < 1 / 3
            else VOLATILITY_LEVELS[1]
            if share < 2 / 3
            else VOLATILITY_LEVELS[2]
        )
    return result


def regime_series(
    bars: Sequence[BarInput],
    atrs: Sequence[float | None],
    trend_events: Iterable[EventLike],
    min_history: int = MIN_HISTORY,
) -> list[Regime | None]:
    """Режим на закрытии бара; ``None``, если тренд или волатильность неизвестны."""
    trends = trend_series(bars, trend_events)
    volatilities = volatility_series(bars, atrs, min_history)
    return [
        Regime(trend, volatility) if trend and volatility else None
        for trend, volatility in zip(trends, volatilities, strict=True)
    ]
