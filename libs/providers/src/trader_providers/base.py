"""Интерфейс поставщика рыночных данных (spec §10) и его общие типы."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from trader_engine.ingest import RawRow, RowError


@dataclass(frozen=True, slots=True)
class ProviderContract:
    """Контракт в терминах поставщика."""

    provider_id: str
    name: str
    asset_code: str
    expiration_date: date
    last_trade_date: date | None
    # Диапазон истории, о котором заявляет поставщик (по дате).
    history_from: date | None
    history_till: date | None


@dataclass(frozen=True, slots=True)
class DailyBar:
    """Итог торгового дня контракта: объём, сделки, оборот и средневзвешенная цена."""

    trade_date: date
    volume: int
    trades: int
    # Оборот за день в валюте расчётов (₽) и средневзвешенная цена: по ним
    # выводится стоимость шага цены (ADR-0007).
    value: Decimal | None = None
    waprice: Decimal | None = None


class HistoricalDataProvider(Protocol):
    """Историческая часть интерфейса; потоковую (realtime) добавит T-Invest.

    Внутренняя система не зависит от формата ISS, брокера или другого API:
    провайдер возвращает контракты и строки ``RawRow`` / ``RowError``.
    """

    code: str

    def list_contracts(
        self,
        asset_code: str,
        *,
        from_year: int,
        to_year: int,
        secid_prefix: str | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[ProviderContract]:
        """Контракты базового актива с датами экспирации, включая истёкшие.

        ``progress(сделано, всего)`` вызывается по ходу перебора; исключение из него
        прерывает перебор.
        """
        ...

    def candle_range(self, provider_id: str) -> tuple[datetime, datetime] | None:
        """Время первой и последней доступной минутной свечи (UTC) или ``None``."""
        ...

    def get_historical_candles(
        self,
        provider_id: str,
        start: date,
        end: date,
        *,
        on_page: Callable[[int], None] | None = None,
    ) -> Iterator[RawRow | RowError]:
        """Минутные свечи за даты ``[start, end]`` включительно, по возрастанию."""
        ...

    def daily_history(self, provider_id: str, start: date, end: date) -> list[DailyBar]:
        """Итоги торговых дней контракта за ``[start, end]`` (включительно)."""
        ...

    def close(self) -> None:
        """Освободить сетевые ресурсы."""
        ...
