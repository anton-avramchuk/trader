"""Свеча инструмента в том виде, в каком она хранится и отдаётся API (ADR-0028)."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Bar:
    """Закрытая свеча: ``timestamp`` — открытие, ``close_time`` — конец (UTC)."""

    timeframe: str
    timestamp: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    # Дата открытия свечи в часовом поясе инструмента (дни, пивоты, walk-forward).
    trading_day: date
