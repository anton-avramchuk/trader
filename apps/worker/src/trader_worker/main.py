import signal
from collections.abc import Callable
from threading import Event

import trader_engine
from sqlalchemy import text
from trader_db import make_engine


def check_database() -> None:
    engine = make_engine()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    finally:
        engine.dispose()


def run(
    stop: Event,
    *,
    check_db: Callable[[], None],
    heartbeat_seconds: float = 30.0,
) -> None:
    """Каркас worker: проверить БД и ждать сигнала остановки.

    Захват задач из очереди появится в задаче про очередь на PostgreSQL.
    """
    check_db()
    print(f"trader-worker: engine {trader_engine.__version__}, database ok", flush=True)
    while not stop.wait(heartbeat_seconds):
        print("trader-worker: alive", flush=True)
    print("trader-worker: stopped", flush=True)


def main() -> None:
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    run(stop, check_db=check_database)
