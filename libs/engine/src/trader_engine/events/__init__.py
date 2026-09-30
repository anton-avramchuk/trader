"""Движки событий: каузальные машины с неизменяемым логом (ADR-0003, ADR-0021)."""

from trader_engine.events.base import (
    Event,
    EventEngine,
    EventStatus,
    run_engine,
)
from trader_engine.events.fibonacci import Fibonacci  # noqa: E402
from trader_engine.events.geometry import Line, fit_line, max_deviation
from trader_engine.events.head_shoulders import HeadShoulders  # noqa: E402
from trader_engine.events.levels import Levels  # noqa: E402
from trader_engine.events.patterns import PatternBase, PatternParams
from trader_engine.events.pivot import Pivot  # noqa: E402
from trader_engine.events.registry import (
    available,
    create,
    describe,
    get,
    hash_params,
    register,
    unregister,
)
from trader_engine.events.resolve import current_events, fingerprint, known_at
from trader_engine.events.reversals import DoubleTriple  # noqa: E402
from trader_engine.events.structure import MarketStructure  # noqa: E402
from trader_engine.events.swing import FixedWindow, ZigZag  # noqa: E402  (регистрация)
from trader_engine.events.trendlines import Trendlines  # noqa: E402

__all__ = [
    "Event",
    "EventEngine",
    "EventStatus",
    "DoubleTriple",
    "Fibonacci",
    "FixedWindow",
    "HeadShoulders",
    "Levels",
    "Line",
    "MarketStructure",
    "PatternBase",
    "PatternParams",
    "Pivot",
    "Trendlines",
    "ZigZag",
    "available",
    "create",
    "current_events",
    "describe",
    "fingerprint",
    "fit_line",
    "get",
    "hash_params",
    "known_at",
    "max_deviation",
    "register",
    "run_engine",
    "unregister",
]
