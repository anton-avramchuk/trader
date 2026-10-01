"""Контракт importer v1 (ADR-0028): тикеры и готовые закрытые свечи."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field

Timeframe = Literal["15m", "1h", "4h", "1d", "1w"]
TIMEFRAMES: tuple[Timeframe, ...] = ("15m", "1h", "4h", "1d", "1w")

DURATIONS: dict[str, timedelta] = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
    "1w": timedelta(days=7),
}


class Ticker(BaseModel):
    """Инструмент источника. Для ядра — абстрактный тикер со свечами."""

    ticker: str = Field(description="Идентификатор инструмента у источника")
    name: str
    currency: str
    tick_size: Decimal = Field(description="Минимальный шаг цены")
    timezone: str = Field(description="IANA-зона биржи (для торговых дней)")
    timeframes: list[Timeframe]
    first_date: date | None = Field(
        default=None, description="Начало истории; `null`, если список не знает"
    )
    last_date: date | None = None


class Candle(BaseModel):
    """Закрытая свеча: `t` — начало свечи (UTC)."""

    t: AwareDatetime
    o: Decimal
    h: Decimal
    l: Decimal  # noqa: E741 — короткие имена полей контракта
    c: Decimal
    v: Decimal


class HistoryPage(BaseModel):
    """Страница истории; ``next_from`` — с чего продолжить, если страница неполная."""

    ticker: str
    tf: Timeframe
    candles: list[Candle]
    next_from: datetime | None = Field(
        default=None,
        description="Начало следующей страницы (UTC); `null` — период закончился",
    )
