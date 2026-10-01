"""Источник данных importer: интерфейс, который реализует каждый рынок."""

from datetime import datetime
from typing import Protocol

from trader_importer.models import HistoryPage, Ticker, Timeframe


class UnknownTicker(Exception):
    """У источника нет такого инструмента."""


class SourceError(Exception):
    """Источник недоступен или ответил ошибкой (для клиента — 502)."""


class HistorySource(Protocol):
    """Тикеры и готовые закрытые свечи таймфрейма за период ``[start, end)``."""

    def tickers(self) -> list[Ticker]: ...

    def ticker(self, ticker: str) -> Ticker:
        """Инструмент с границами истории; ``UnknownTicker`` — если нет."""
        ...

    def history(
        self, ticker: str, tf: Timeframe, start: datetime, end: datetime, limit: int
    ) -> HistoryPage: ...

    def close(self) -> None: ...
