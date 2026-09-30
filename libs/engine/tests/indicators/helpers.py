"""Общие генераторы баров для тестов индикаторов."""

from datetime import UTC, date, datetime, timedelta

from hypothesis import strategies as st

from trader_engine.indicators import BarInput

START = datetime(2026, 9, 28, 4, tzinfo=UTC)
BARS_PER_DAY = 24


def make_bars(rows: list[tuple[float, float, float, float, float]]) -> list[BarInput]:
    """Бары из кортежей ``(open, high, low, close, volume)`` с шагом 15 минут."""
    bars: list[BarInput] = []
    for index, (open_, high, low, close, volume) in enumerate(rows):
        start = START + timedelta(minutes=15 * index)
        bars.append(
            BarInput(
                timestamp=start,
                close_time=start + timedelta(minutes=15),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                trading_day=date(2026, 9, 28) + timedelta(days=index // BARS_PER_DAY),
            )
        )
    return bars


def closes(values: list[float], volume: float = 1.0) -> list[BarInput]:
    """Бары, у которых open = high = low = close (для проверки по цене закрытия)."""
    return make_bars([(v, v, v, v, volume) for v in values])


@st.composite
def bar_series(
    draw: st.DrawFn, min_size: int = 1, max_size: int = 120
) -> list[BarInput]:
    size = draw(st.integers(min_value=min_size, max_value=max_size))
    rows: list[tuple[float, float, float, float, float]] = []
    price = draw(st.integers(min_value=50, max_value=150))
    for _ in range(size):
        step = draw(st.integers(min_value=-5, max_value=5))
        price = max(price + step, 1)
        spread_up = draw(st.integers(min_value=0, max_value=4))
        spread_down = draw(st.integers(min_value=0, max_value=4))
        high = float(price + spread_up)
        low = float(max(price - spread_down, 1))
        volume = float(draw(st.integers(min_value=0, max_value=1000)))
        rows.append((float(price), high, low, float(price), volume))
    return make_bars(rows)


def sample_bars(size: int = 80) -> list[BarInput]:
    """Детерминированный ряд с разбросом цен и объёмов."""
    rows = [
        (
            100.0 + i % 7,
            103.0 + i % 5,
            98.0 - i % 3,
            100.0 + (i * 3) % 11,
            10.0 + i % 13,
        )
        for i in range(size)
    ]
    return make_bars(rows)
