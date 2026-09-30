import logging
import os
import signal
import socket
from threading import Event

import trader_engine
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from trader_db import ensure_schedule, make_engine

from trader_worker.file_sources import ImportSettings
from trader_worker.registry import build_registry
from trader_worker.runner import Worker, WorkerConfig
from trader_worker.step_price_jobs import STEP_PRICES_ALL_JOB_TYPE


class WorkerSettings(BaseSettings):
    """Настройки worker'а; переменные окружения с префиксом ``TRADER_WORKER_``."""

    model_config = SettingsConfigDict(env_prefix="TRADER_WORKER_", extra="ignore")

    worker_id: str = f"{socket.gethostname()}-{os.getpid()}"
    poll_interval: float = 1.0
    heartbeat_interval: float = 5.0
    stale_after: float = 30.0
    housekeeping_interval: float = 5.0

    def to_config(self) -> WorkerConfig:
        return WorkerConfig(
            worker_id=self.worker_id,
            poll_interval=self.poll_interval,
            heartbeat_interval=self.heartbeat_interval,
            stale_after=self.stale_after,
            housekeeping_interval=self.housekeeping_interval,
        )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    engine = make_engine()
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    logging.getLogger("trader_worker").info(
        "trader-worker: engine %s, database ok", trader_engine.__version__
    )

    session_factory = sessionmaker(engine, expire_on_commit=False)
    with session_factory() as session:
        # Стоимость шага цены обновляется раз в сутки (ADR-0007).
        ensure_schedule(
            session, "iss-step-prices-daily", STEP_PRICES_ALL_JOB_TYPE, 86_400
        )
        session.commit()

    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    worker = Worker(
        session_factory,
        build_registry(session_factory, ImportSettings().import_dir),
        WorkerSettings().to_config(),
    )
    worker.run(stop)
    engine.dispose()
