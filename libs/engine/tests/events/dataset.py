"""Фиксированный датасет для регрессионных и интеграционных тестов движков."""

import random
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from trader_engine.indicators import BarInput

DAYS = [
    date(2026, 9, 24),
    date(2026, 9, 25),
    date(2026, 9, 28),
    date(2026, 9, 29),
    date(2026, 9, 30),
    date(2026, 10, 1),
    date(2026, 10, 2),
    date(2026, 10, 5),
]
BARS_PER_DAY = 32


def fixed_bars(seed: int = 20260928) -> list[BarInput]:
    """8 торговых дней по 32 бара 15m: воспроизводимое блуждание с волнами."""
    rng = random.Random(seed)
    bars: list[BarInput] = []
    price = 100.0
    for day_index, day in enumerate(DAYS):
        start = datetime(day.year, day.month, day.day, 4, tzinfo=UTC)
        for i in range(BARS_PER_DAY):
            wave = 2.5 if (day_index * BARS_PER_DAY + i) // 24 % 2 == 0 else -2.5
            open_ = price
            close = round(
                max(open_ + wave * rng.uniform(0.2, 1.0) + rng.gauss(0, 1.2), 5), 2
            )
            high = round(max(open_, close) + rng.uniform(0, 1.5), 2)
            low = round(max(min(open_, close) - rng.uniform(0, 1.5), 1), 2)
            opened = start + timedelta(minutes=15 * i)
            bars.append(
                BarInput(
                    timestamp=opened,
                    close_time=opened + timedelta(minutes=15),
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=float(rng.randint(1, 100)),
                    trading_day=day,
                )
            )
            price = close
    return bars


def perturb_future(bars: list[BarInput], cut: int, seed: int = 7) -> list[BarInput]:
    """Те же бары до ``cut``, а «будущее» после него — заведомо другое."""
    rng = random.Random(seed)
    changed = list(bars[:cut])
    for bar in bars[cut:]:
        close = rng.uniform(1, 400)
        high = close + rng.uniform(0, 50)
        low = max(close - rng.uniform(0, 50), 0.5)
        changed.append(replace(bar, open=close, high=high, low=low, close=close))
    return changed
