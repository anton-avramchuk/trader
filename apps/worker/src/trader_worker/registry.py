"""Сборка реестра обработчиков worker'а."""

from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from trader_worker.file_sources import file_source
from trader_worker.handlers import HandlerRegistry, default_registry
from trader_worker.import_jobs import register_import_job


def build_registry(
    session_factory: sessionmaker[Session], import_dir: Path
) -> HandlerRegistry:
    """Демо-задачи и импорт файлов (``import.file``)."""
    registry = default_registry()
    register_import_job(
        registry,
        "import.file",
        session_factory,
        file_source(session_factory, import_dir),
    )
    return registry
