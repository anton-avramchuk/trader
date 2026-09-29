"""Цикл worker'а: захват задач, heartbeat, планировщик, восстановление зависших."""

import logging
import time
import traceback
from dataclasses import dataclass
from threading import Event, Thread
from typing import Any

from sqlalchemy.orm import Session, sessionmaker
from trader_db import (
    cancel_running_job,
    claim_next_job,
    complete_job,
    fail_job,
    release_job,
    requeue_stale_jobs,
    run_due_schedules,
    touch_job,
)
from trader_db.models import Job

from trader_worker.handlers import (
    HandlerRegistry,
    JobCancelled,
    JobContext,
    JobInterrupted,
    JobLost,
)

log = logging.getLogger("trader_worker")

_ERROR_LIMIT = 4000


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    worker_id: str
    poll_interval: float = 1.0
    heartbeat_interval: float = 5.0
    # Задача без heartbeat дольше этого срока считается зависшей.
    stale_after: float = 30.0
    housekeeping_interval: float = 5.0


class _Heartbeat(Thread):
    """Обновляет heartbeat выполняющейся задачи и следит за запросом отмены."""

    def __init__(self, worker: "Worker", context: JobContext) -> None:
        super().__init__(name=f"heartbeat-{context.job_id}", daemon=True)
        self._worker = worker
        self._context = context
        self._done = Event()

    def run(self) -> None:
        interval = self._worker.config.heartbeat_interval
        while not self._done.wait(interval):
            try:
                cancel = self._worker.touch(self._context.job_id)
            except Exception:
                log.exception(
                    "Не удалось обновить heartbeat задачи %s", self._context.job_id
                )
                continue
            if cancel is None:
                self._context.lost.set()
                return
            if cancel:
                self._context.cancel_requested.set()

    def stop(self) -> None:
        self._done.set()
        self.join(timeout=5)


class Worker:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        registry: HandlerRegistry,
        config: WorkerConfig,
    ) -> None:
        self._sessions = session_factory
        self._registry = registry
        self.config = config

    # --- короткие транзакции ------------------------------------------------

    def touch(
        self,
        job_id: int,
        *,
        progress: float | None = None,
        message: str | None = None,
    ) -> bool | None:
        with self._sessions() as session:
            cancel = touch_job(
                session,
                job_id,
                self.config.worker_id,
                progress=progress,
                message=message,
            )
            session.commit()
        return cancel

    def claim(self) -> Job | None:
        with self._sessions() as session:
            job = claim_next_job(session, self.config.worker_id)
            session.commit()
            return job

    def housekeeping(self) -> None:
        """Поставить задачи по расписаниям и вернуть зависшие."""
        with self._sessions() as session:
            created = run_due_schedules(session)
            requeued, failed = requeue_stale_jobs(session, self.config.stale_after)
            session.commit()
        if created:
            log.info("Расписание поставило задач: %s", len(created))
        if requeued or failed:
            log.warning(
                "Зависшие задачи: возвращено %s, провалено %s", requeued, failed
            )

    # --- выполнение ---------------------------------------------------------

    def run_once(self, stop: Event) -> bool:
        """Взять и выполнить одну задачу; ``False``, если очередь пуста."""
        job = self.claim()
        if job is None:
            return False
        self._execute(job, stop)
        return True

    def _execute(self, job: Job, stop: Event) -> None:
        job_id = job.id
        context = JobContext(
            job_id=job_id,
            job_type=job.type,
            params=dict(job.params),
            attempt=job.attempts,
            _sink=lambda fraction, message: self.touch(
                job_id, progress=fraction, message=message
            ),
            _stop=stop,
        )
        handler = self._registry.get(job.type)
        heartbeat = _Heartbeat(self, context)
        heartbeat.start()
        outcome: str
        payload: Any = None
        try:
            if handler is None:
                outcome, payload = "fail", f"Нет обработчика для типа {job.type!r}"
            else:
                outcome, payload = "complete", handler(context)
        except JobCancelled:
            outcome = "cancel"
        except JobInterrupted:
            outcome = "release"
        except JobLost:
            outcome = "lost"
        except Exception:
            outcome, payload = "fail", traceback.format_exc()[-_ERROR_LIMIT:]
        finally:
            heartbeat.stop()
        self._finish(job_id, outcome, payload)

    def _finish(self, job_id: int, outcome: str, payload: Any) -> None:
        worker_id = self.config.worker_id
        if outcome == "lost":
            log.warning("Задача %s потеряна, результат отброшен", job_id)
            return
        try:
            with self._sessions() as session:
                if outcome == "complete":
                    accepted = complete_job(session, job_id, worker_id, payload)
                elif outcome == "cancel":
                    accepted = cancel_running_job(session, job_id, worker_id)
                elif outcome == "release":
                    accepted = release_job(session, job_id, worker_id)
                else:
                    accepted = fail_job(session, job_id, worker_id, str(payload))
                session.commit()
        except Exception:
            # Например, результат не сериализуется в JSON: помечаем задачу проваленной.
            log.exception("Не удалось сохранить итог задачи %s", job_id)
            with self._sessions() as session:
                accepted = fail_job(
                    session,
                    job_id,
                    worker_id,
                    "Не удалось сохранить результат:\n"
                    + traceback.format_exc()[-_ERROR_LIMIT:],
                )
                session.commit()
        if not accepted:
            log.warning(
                "Итог задачи %s не принят: задача уже не принадлежит worker'у", job_id
            )

    def run(self, stop: Event) -> None:
        """Основной цикл до сигнала остановки."""
        log.info(
            "Worker %s запущен, обработчики: %s",
            self.config.worker_id,
            self._registry.types(),
        )
        next_housekeeping = 0.0
        while not stop.is_set():
            now = time.monotonic()
            if now >= next_housekeeping:
                next_housekeeping = now + self.config.housekeeping_interval
                try:
                    self.housekeeping()
                except Exception:
                    log.exception("Ошибка обслуживания очереди")
            try:
                if self.run_once(stop):
                    continue
            except Exception:
                log.exception("Ошибка при обработке очереди")
            stop.wait(self.config.poll_interval)
        log.info("Worker %s остановлен", self.config.worker_id)
