"""Источник строк из файла в каталоге импорта: CSV, JSON, Parquet."""

from collections.abc import Iterator
from itertools import chain
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.orm import Session, sessionmaker
from trader_db import get_preset
from trader_engine.file_import import (
    ColumnRef,
    FileMapping,
    Record,
    SourceError,
    check_record_columns,
    map_records,
    read_csv_records,
    read_json_records,
)
from trader_engine.ingest import RawRow, RowError

from trader_worker.handlers import JobContext, JobFailed
from trader_worker.import_jobs import ImportSource, SourceFactory

FORMATS = ("csv", "json", "parquet")
_EXTENSIONS = {
    ".csv": "csv",
    ".txt": "csv",
    ".json": "json",
    ".ndjson": "json",
    ".parquet": "parquet",
}


class ImportSettings(BaseSettings):
    """Каталог загруженных файлов; общий для api и worker (``TRADER_IMPORT_DIR``)."""

    model_config = SettingsConfigDict(env_prefix="TRADER_", extra="ignore")

    import_dir: Path = Path("data/imports")


def resolve_import_path(import_dir: Path, name: str) -> Path:
    """Файл внутри каталога импорта; выход за его пределы запрещён."""
    base = import_dir.resolve()
    target = (base / name).resolve()
    if base not in target.parents:
        raise JobFailed(f"Файл {name!r} лежит вне каталога импорта")
    if not target.is_file():
        raise JobFailed(f"Файл {name!r} не найден в каталоге импорта")
    return target


def _format_of(path: Path, requested: str | None) -> str:
    fmt = requested or _EXTENSIONS.get(path.suffix.lower())
    if fmt not in FORMATS:
        raise JobFailed(
            f"Неизвестный формат {fmt or path.suffix!r}; "
            f"поддерживаются: {', '.join(FORMATS)}"
        )
    return fmt


def _load_mapping(
    session_factory: sessionmaker[Session], params: dict[str, Any]
) -> FileMapping:
    preset, inline = params.get("preset"), params.get("mapping")
    if (preset is None) == (inline is None):
        raise JobFailed("Укажите ровно одно из: preset (имя пресета) или mapping")
    if preset is not None:
        with session_factory() as session:
            mapping = get_preset(session, str(preset))
    else:
        mapping = FileMapping.model_validate(inline)
    overrides = params.get("overrides")
    if overrides:
        return FileMapping.model_validate({**mapping.model_dump(), **overrides})
    return mapping


def _csv_rows(path: Path, mapping: FileMapping) -> Iterator[RawRow | RowError]:
    try:
        with path.open("r", encoding=mapping.encoding, newline="") as handle:
            yield from map_records(read_csv_records(handle, mapping), mapping)
    except UnicodeDecodeError as error:
        raise SourceError(
            f"Не удалось прочитать файл в кодировке {mapping.encoding}: "
            f"{error.reason} (байт {error.start}). "
            "Задайте другую кодировку, например cp1251"
        ) from error


def _json_rows(path: Path, mapping: FileMapping) -> Iterator[RawRow | RowError]:
    try:
        text = path.read_text(encoding=mapping.encoding)
    except UnicodeDecodeError as error:
        raise SourceError(
            f"Не удалось прочитать файл в кодировке {mapping.encoding}: {error.reason}"
        ) from error
    records = read_json_records(text)
    first = next(records, None)
    if first is None:
        return
    check_record_columns(first.values, mapping)
    yield from map_records(chain([first], records), mapping)


def _parquet_records(frame: Any) -> Iterator[Record]:
    for index, row in enumerate(frame.iter_rows(named=True), start=1):
        values: dict[ColumnRef, Any] = {name: value for name, value in row.items()}
        yield Record(index, None, values)


def _parquet_rows(path: Path, mapping: FileMapping) -> Iterator[RawRow | RowError]:
    import polars as pl

    try:
        frame = pl.read_parquet(path)
    except Exception as error:
        raise SourceError(f"Не удалось прочитать Parquet: {error}") from error
    check_record_columns({name: None for name in frame.columns}, mapping)
    records = _parquet_records(frame)
    yield from map_records(records, mapping)


def file_source(
    session_factory: sessionmaker[Session], import_dir: Path
) -> SourceFactory:
    """Источник для задачи ``import.file``.

    Параметры задачи: ``file`` (имя внутри каталога импорта), ``format``
    (``csv`` / ``json`` / ``parquet``, по умолчанию по расширению), ровно одно из
    ``preset`` / ``mapping`` и необязательные ``overrides`` (поля маппинга,
    например ``{"timezone": "UTC"}``).
    """

    def factory(context: JobContext) -> ImportSource:
        params = context.params
        name = params.get("file")
        if not isinstance(name, str) or not name:
            raise JobFailed("Не задан параметр file (имя файла в каталоге импорта)")
        path = resolve_import_path(import_dir, name)
        fmt = _format_of(path, params.get("format"))
        mapping = _load_mapping(session_factory, params)
        readers = {"csv": _csv_rows, "json": _json_rows, "parquet": _parquet_rows}
        return ImportSource(
            provider_code="csv",
            source_type="file",
            rows=readers[fmt](path, mapping),
            source_name=path.name,
        )

    return factory
