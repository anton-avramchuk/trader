"""Общие помощники тестов worker."""

import time
from collections.abc import Callable
from threading import Thread
from typing import Any

from sqlalchemy.orm import Session, sessionmaker
from trader_db import enqueue_job, get_job
from trader_db.models import Job


def enqueue(
    session_factory: sessionmaker[Session],
    job_type: str,
    params: dict[str, Any] | None = None,
) -> int:
    with session_factory() as session:
        job_id = enqueue_job(session, job_type, params)
        session.commit()
    return job_id


def load(session_factory: sessionmaker[Session], job_id: int) -> Job:
    with session_factory() as session:
        job = get_job(session, job_id)
        assert job is not None
        return job


def wait_until(predicate: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("условие не выполнилось за отведённое время")
        time.sleep(0.02)


def in_thread(target: Callable[..., object], *args: object) -> Thread:
    thread = Thread(target=target, args=args, daemon=True)
    thread.start()
    return thread
