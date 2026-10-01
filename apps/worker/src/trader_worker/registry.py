"""Сборка реестра обработчиков worker'а."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker
from trader_providers import IssClient

from trader_worker.aggregate_jobs import (
    AGGREGATE_JOB_TYPE,
    Clock,
    make_aggregate_handler,
    utc_now,
)
from trader_worker.backtest_jobs import BACKTEST_JOB_TYPE, make_backtest_handler
from trader_worker.engine_jobs import ENGINE_RUN_JOB_TYPE, make_engine_run_handler
from trader_worker.file_sources import file_source
from trader_worker.handlers import HandlerRegistry, default_registry
from trader_worker.import_jobs import register_import_job
from trader_worker.iss_jobs import (
    DEFAULT_CHUNK_DAYS,
    IMPORT_JOB_TYPE,
    SYNC_JOB_TYPE,
    ProviderFactory,
    make_iss_import_handler,
    make_sync_root_handler,
    msk_today,
)
from trader_worker.step_price_jobs import (
    STEP_PRICES_ALL_JOB_TYPE,
    STEP_PRICES_JOB_TYPE,
    make_step_prices_all_handler,
    make_step_prices_handler,
)
from trader_worker.verify_jobs import VERIFY_JOB_TYPE, make_verify_handler


def build_registry(
    session_factory: sessionmaker[Session],
    import_dir: Path,
    provider_factory: ProviderFactory = IssClient,
    *,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
    today: Callable[[], date] = msk_today,
    clock: Clock = utc_now,
) -> HandlerRegistry:
    """Демо-задачи, импорт файлов (``import.file``) и задачи MOEX ISS."""
    registry = default_registry()
    register_import_job(
        registry,
        "import.file",
        session_factory,
        file_source(session_factory, import_dir),
    )
    registry.register(SYNC_JOB_TYPE)(
        make_sync_root_handler(session_factory, provider_factory, today=today)
    )
    registry.register(IMPORT_JOB_TYPE)(
        make_iss_import_handler(
            session_factory, provider_factory, chunk_days=chunk_days, today=today
        )
    )
    registry.register(AGGREGATE_JOB_TYPE)(
        make_aggregate_handler(session_factory, now=clock)
    )
    registry.register(STEP_PRICES_JOB_TYPE)(
        make_step_prices_handler(session_factory, provider_factory, today=today)
    )
    registry.register(STEP_PRICES_ALL_JOB_TYPE)(
        make_step_prices_all_handler(session_factory, today=today)
    )
    registry.register(VERIFY_JOB_TYPE)(make_verify_handler(session_factory))
    registry.register(ENGINE_RUN_JOB_TYPE)(make_engine_run_handler(session_factory))
    registry.register(BACKTEST_JOB_TYPE)(make_backtest_handler(session_factory))
    return registry
