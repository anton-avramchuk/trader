"""Индикаторы: плагины, реестр, кэш (ADR-0003, ADR-0010)."""

from trader_engine.indicators import (  # регистрация встроенных плагинов
    momentum,
    trend,
    volatility,
    volume,
)
from trader_engine.indicators.base import (
    BarInput,
    Indicator,
    IndicatorSeries,
    run,
)
from trader_engine.indicators.cache import CacheStats, IndicatorCache
from trader_engine.indicators.mtf import (
    Projection,
    project,
    validate_source_timeframe,
)
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
    "Projection",
    "available",
    "create",
    "describe",
    "get",
    "params_hash",
    "project",
    "momentum",
    "register",
    "run",
    "trend",
    "unregister",
    "validate_source_timeframe",
    "volatility",
    "volume",
]
