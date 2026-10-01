"""Сборка реестра обработчиков worker'а."""

from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from trader_worker.backtest_jobs import BACKTEST_JOB_TYPE, make_backtest_handler
from trader_worker.candles_jobs import CANDLES_LOAD_JOB_TYPE, make_candles_load_handler
from trader_worker.engine_jobs import ENGINE_RUN_JOB_TYPE, make_engine_run_handler
from trader_worker.handlers import HandlerRegistry, default_registry
from trader_worker.importer_client import ImporterClient


def build_registry(
    session_factory: sessionmaker[Session],
    importer_factory: Any = ImporterClient,
) -> HandlerRegistry:
    """Демо-задачи, загрузка свечей из importer, прогоны движков и бэктесты."""
    registry = default_registry()
    registry.register(CANDLES_LOAD_JOB_TYPE)(
        make_candles_load_handler(session_factory, importer_factory)
    )
    registry.register(ENGINE_RUN_JOB_TYPE)(make_engine_run_handler(session_factory))
    registry.register(BACKTEST_JOB_TYPE)(make_backtest_handler(session_factory))
    return registry
