"""Индикаторы: плагины, реестр, кэш (ADR-0003, ADR-0010)."""

from trader_engine.indicators import trend  # регистрация встроенных плагинов
from trader_engine.indicators.base import (
    BarInput,
    Indicator,
    IndicatorSeries,
    run,
)
from trader_engine.indicators.cache import CacheStats, IndicatorCache
from trader_engine.indicators.registry import (
    available,
    create,
    describe,
    get,
    params_hash,
    register,
    unregister,
)

__all__ = [
    "BarInput",
    "CacheStats",
    "Indicator",
    "IndicatorCache",
    "IndicatorSeries",
    "available",
    "create",
    "describe",
    "get",
    "params_hash",
    "register",
    "run",
    "trend",
    "unregister",
]
