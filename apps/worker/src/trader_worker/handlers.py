"""Контекст выполняемой задачи и реестр обработчиков."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event
from typing import Any


class JobCancelled(Exception):
    """Пользователь запросил отмену; обработчик остановился."""


class JobInterrupted(Exception):
    """Worker останавливается; задача вернётся в очередь без потери попытки."""


class JobLost(Exception):
    """Задача больше не принадлежит этому worker'у (возвращена в очередь и т. п.)."""


class JobFailed(Exception):
    """Ожидаемая ошибка (неверный файл, маппинг…): в задачу пишется только сообщение."""


ProgressSink = Callable[[float | None, str | None], bool | None]


@dataclass
class JobContext:
    """То, что видит обработчик: параметры, прогресс и проверки остановки.

    Обработчик обязан регулярно вызывать :meth:`report_progress` или
    :meth:`check`, иначе отмена и штатная остановка ждут его завершения.
    """

    job_id: int
    job_type: str
    params: dict[str, Any]
    attempt: int
    _sink: ProgressSink
    _stop: Event
    cancel_requested: Event = field(default_factory=Event)
    lost: Event = field(default_factory=Event)

    def check(self) -> None:
        """Бросить исключение, если задачу нужно прервать."""
        if self.lost.is_set():
            raise JobLost(f"Задача {self.job_id} потеряна")
        if self.cancel_requested.is_set():
            raise JobCancelled(f"Задача {self.job_id} отменена")
        if self._stop.is_set():
            raise JobInterrupted(f"Worker остановлен во время задачи {self.job_id}")

    def report_progress(
        self, fraction: float | None = None, message: str | None = None
    ) -> None:
        """Сохранить прогресс (доля 0..1) и сообщение; заодно это heartbeat."""
        self.check()
        cancel = self._sink(fraction, message)
        if cancel is None:
            self.lost.set()
        elif cancel:
            self.cancel_requested.set()
        self.check()

    def wait(self, seconds: float) -> None:
        """Спать, прерываясь при отмене/остановке (шаг проверки 0.1 с)."""
        deadline = time.monotonic() + seconds
        while (remaining := deadline - time.monotonic()) > 0:
            self.check()
            time.sleep(min(0.1, remaining))
        self.check()


Handler = Callable[[JobContext], dict[str, Any] | None]


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, job_type: str) -> Callable[[Handler], Handler]:
        def decorator(handler: Handler) -> Handler:
            if job_type in self._handlers:
                raise ValueError(f"Обработчик {job_type!r} уже зарегистрирован")
            self._handlers[job_type] = handler
            return handler

        return decorator

    def get(self, job_type: str) -> Handler | None:
        return self._handlers.get(job_type)

    def types(self) -> list[str]:
        return sorted(self._handlers)


def demo_sleep(context: JobContext) -> dict[str, Any]:
    """Демонстрационная задача: ``seconds`` секунд за ``steps`` шагов с прогрессом."""
    seconds = float(context.params.get("seconds", 3))
    steps = max(1, int(context.params.get("steps", 10)))
    for step in range(1, steps + 1):
        context.wait(seconds / steps)
        context.report_progress(step / steps, f"шаг {step} из {steps}")
    return {"slept": seconds, "steps": steps}


def default_registry() -> HandlerRegistry:
    registry = HandlerRegistry()
    registry.register("demo.sleep")(demo_sleep)
    return registry
