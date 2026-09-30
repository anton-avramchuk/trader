"""Движки событий: каузальные машины с неизменяемым логом (ADR-0003, ADR-0021)."""

from trader_engine.events.base import (
    Event,
    EventEngine,
    EventStatus,
    run_engine,
)
from trader_engine.events.fibonacci import Fibonacci  # noqa: E402
from trader_engine.events.levels import Levels  # noqa: E402
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
from trader_engine.events.structure import MarketStructure  # noqa: E402
from trader_engine.events.swing import FixedWindow, ZigZag  # noqa: E402  (регистрация)

__all__ = [
    "Event",
    "EventEngine",
    "EventStatus",
    "Fibonacci",
    "FixedWindow",
    "Levels",
    "MarketStructure",
    "Pivot",
    "ZigZag",
    "available",
    "create",
    "current_events",
    "describe",
    "fingerprint",
    "get",
    "hash_params",
    "known_at",
    "register",
    "run_engine",
    "unregister",
]
