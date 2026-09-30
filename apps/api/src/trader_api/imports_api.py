"""REST импорта: запуск загрузок, файлы, пресеты, отчёты, конфликты (ADR-0015).

Тяжёлая работа выполняется worker'ом: маршруты ставят задачи (`import.iss`,
`import.file`) и отдают их id — ход выполнения смотрится через `/jobs/{id}`
или WebSocket.
"""

import codecs
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from trader_db import (
    delete_preset,
    enqueue_aggregation,
    enqueue_statement,
    extend_dataset_version,
    list_presets,
    resolve_conflicts,
    save_preset,
)
from trader_db.models import (
    Contract,
    DataImport,
    DataImportError,
    DataProvider,
    ImportConflict,
    RawCandle1m,
)
from trader_engine.file_import import FileMapping

from trader_api.deps import DbSession, Settings
from trader_api.jobs import JobOut

router = APIRouter()

NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Не найдено"}}
_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,199}$")
_CHUNK = 1024 * 1024


# --- импорты и отчёты --------------------------------------------------------


class ImportOut(BaseModel):
    id: int
    contract_id: int
    provider: str
    kind: str = Field(description="`import` или `conflict_resolution`")
    source_type: str
    source_name: str | None
    status: str = Field(description="running, completed, failed")
    report: dict[str, Any] = Field(
        description="Отчёт: строки, дубликаты, конфликты, покрытие, диапазон (ADR-0015)"
    )
    error: str | None
    resolves_import_id: int | None
    created_at: datetime
    finished_at: datetime | None


class ImportErrorOut(BaseModel):
    row_number: int | None
    raw_row: str | None
    reason_code: str
    message: str


def _import_out(row: DataImport, provider: str) -> ImportOut:
    return ImportOut(
        id=row.id,
        contract_id=row.contract_id,
        provider=provider,
        kind=row.kind,
        source_type=row.source_type,
        source_name=row.source_name,
        status=row.status,
        report=row.report,
        error=row.error,
        resolves_import_id=row.resolves_import_id,
        created_at=row.created_at,
        finished_at=row.finished_at,
    )


async def _find_import(session: AsyncSession, import_id: int) -> ImportOut:
    row = (
        await session.execute(
            select(DataImport, DataProvider.code)
            .join(DataProvider, DataProvider.id == DataImport.provider_id)
            .where(DataImport.id == import_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "Импорт не найден")
    return _import_out(row[0], row[1])


@router.get(
    "/imports",
    response_model=list[ImportOut],
    tags=["imports"],
    operation_id="listImports",
    summary="Импорты",
    description="Новые первыми.",
)
async def list_imports(
    session: DbSession,
    contract_id: int | None = None,
    status: str | None = None,
    kind: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[ImportOut]:
    query = (
        select(DataImport, DataProvider.code)
        .join(DataProvider, DataProvider.id == DataImport.provider_id)
        .order_by(DataImport.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if contract_id is not None:
        query = query.where(DataImport.contract_id == contract_id)
    if status is not None:
        query = query.where(DataImport.status == status)
    if kind is not None:
        query = query.where(DataImport.kind == kind)
    return [_import_out(row, code) for row, code in await session.execute(query)]


@router.get(
    "/imports/{import_id}",
    response_model=ImportOut,
    tags=["imports"],
    operation_id="getImport",
    summary="Импорт с отчётом",
    responses=NOT_FOUND,
)
async def get_import(import_id: int, session: DbSession) -> ImportOut:
    return await _find_import(session, import_id)


@router.get(
    "/imports/{import_id}/errors",
    response_model=list[ImportErrorOut],
    tags=["imports"],
    operation_id="listImportErrors",
    summary="Отклонённые строки импорта",
    responses=NOT_FOUND,
)
async def list_import_errors(
    import_id: int,
    session: DbSession,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[ImportErrorOut]:
    await _find_import(session, import_id)
    rows = await session.scalars(
        select(DataImportError)
        .where(DataImportError.data_import_id == import_id)
        .order_by(DataImportError.row_number, DataImportError.id)
        .limit(limit)
        .offset(offset)
    )
    return [
        ImportErrorOut(
            row_number=row.row_number,
            raw_row=row.raw_row,
            reason_code=row.reason_code,
            message=row.message,
        )
        for row in rows
    ]


# --- запуск импортов ---------------------------------------------------------


class IssImportIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    date_from: date | None = Field(
        default=None, alias="from", description="Начало окна (включительно)"
    )
    date_till: date | None = Field(
        default=None, alias="till", description="Конец окна (включительно)"
    )

    @model_validator(mode="after")
    def _ordered(self) -> "IssImportIn":
        if self.date_from and self.date_till and self.date_from > self.date_till:
            raise ValueError("from позже till")
        return self


class FileImportIn(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"file": "NG-2020.csv", "preset": "finam"}]}
    )

    file: str = Field(
        description="Имя файла, загруженного через `PUT /import-files/{name}`"
    )
    format: str | None = Field(
        default=None,
        pattern="^(csv|json|parquet)$",
        description="По умолчанию — по расширению",
    )
    preset: str | None = Field(default=None, description="Имя пресета маппинга")
    mapping: FileMapping | None = Field(default=None, description="Маппинг без пресета")
    overrides: dict[str, Any] | None = Field(
        default=None, description="Поля маппинга, заменяемые для этого запуска"
    )
    allow_non_positive_prices: bool = False

    @model_validator(mode="after")
    def _exactly_one_mapping(self) -> "FileImportIn":
        if (self.preset is None) == (self.mapping is None):
            raise ValueError("Укажите ровно одно из: preset или mapping")
        return self


async def _require_contract(session: AsyncSession, contract_id: int) -> None:
    if await session.get(Contract, contract_id) is None:
        raise HTTPException(404, "Контракт не найден")


@router.post(
    "/contracts/{contract_id}/imports/iss",
    response_model=JobOut,
    status_code=201,
    tags=["imports"],
    operation_id="startIssImport",
    summary="Загрузить историю из ISS",
    description=(
        "Ставит `import.iss`. Загрузка возобновляемая: повторный запуск "
        "догружает только недостающее."
    ),
    responses=NOT_FOUND,
)
async def start_iss_import(
    contract_id: int, body: IssImportIn, session: DbSession
) -> Any:
    await _require_contract(session, contract_id)
    params: dict[str, Any] = {"contract_id": contract_id}
    if body.date_from:
        params["from"] = body.date_from.isoformat()
    if body.date_till:
        params["till"] = body.date_till.isoformat()
    job = (await session.scalars(enqueue_statement("import.iss", params))).one()
    await session.commit()
    return job


@router.post(
    "/contracts/{contract_id}/imports/file",
    response_model=JobOut,
    status_code=201,
    tags=["imports"],
    operation_id="startFileImport",
    summary="Импортировать загруженный файл",
    description="Ставит `import.file` для файла из каталога импорта.",
    responses=NOT_FOUND,
)
async def start_file_import(
    contract_id: int, body: FileImportIn, session: DbSession, settings: Settings
) -> Any:
    await _require_contract(session, contract_id)
    if not _safe_path(settings.import_dir, body.file).is_file():
        raise HTTPException(404, f"Файл {body.file!r} не загружен")
    params: dict[str, Any] = {
        "contract_id": contract_id,
        "file": body.file,
        "allow_non_positive_prices": body.allow_non_positive_prices,
    }
    if body.format:
        params["format"] = body.format
    if body.preset:
        params["preset"] = body.preset
    if body.mapping:
        params["mapping"] = body.mapping.model_dump(mode="json")
    if body.overrides:
        params["overrides"] = body.overrides
    job = (await session.scalars(enqueue_statement("import.file", params))).one()
    await session.commit()
    return job


# --- файлы -------------------------------------------------------------------


class FileOut(BaseModel):
    name: str
    size: int
    modified_at: datetime


def _safe_path(import_dir: Path, name: str) -> Path:
    if not _FILE_NAME.match(name) or ".." in name:
        raise HTTPException(422, "Недопустимое имя файла")
    return import_dir / name


def _file_out(path: Path) -> FileOut:
    stat = path.stat()
    return FileOut(
        name=path.name,
        size=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime).astimezone(),
    )


@router.get(
    "/import-files",
    response_model=list[FileOut],
    tags=["files"],
    operation_id="listImportFiles",
    summary="Загруженные файлы",
)
async def list_import_files(settings: Settings) -> list[FileOut]:
    if not settings.import_dir.is_dir():
        return []
    return sorted(
        (_file_out(p) for p in settings.import_dir.iterdir() if p.is_file()),
        key=lambda f: f.name,
    )


@router.put(
    "/import-files/{name}",
    response_model=FileOut,
    tags=["files"],
    operation_id="uploadImportFile",
    summary="Загрузить файл для импорта",
    description=(
        "Тело запроса — содержимое файла как есть (`application/octet-stream`). "
        "Существующий файл с тем же именем заменяется."
    ),
    responses={
        413: {"description": "Файл больше лимита"},
        422: {"description": "Недопустимое имя"},
    },
)
async def upload_import_file(
    name: str, request: Request, settings: Settings
) -> FileOut:
    target = _safe_path(settings.import_dir, name)
    settings.import_dir.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    size = 0
    try:
        with partial.open("wb") as handle:
            async for chunk in request.stream():
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(413, "Файл больше допустимого размера")
                handle.write(chunk)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return _file_out(target)


class FilePreview(BaseModel):
    name: str
    size: int
    encoding: str
    lines: list[str] = Field(description="Первые строки файла как текст")
    delimiter: str | None = Field(
        description="Предполагаемый разделитель CSV (`,` `;` таб `|`) или null"
    )


def _guess_delimiter(lines: list[str]) -> str | None:
    sample = next((line for line in lines if line.strip()), "")
    counts = {d: sample.count(d) for d in (",", ";", "\t", "|")}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] else None


@router.get(
    "/import-files/{name}/preview",
    response_model=FilePreview,
    tags=["files"],
    operation_id="previewImportFile",
    summary="Первые строки загруженного файла",
    description=(
        "Для настройки маппинга колонок. Только текстовые форматы (CSV, JSON); "
        "кодировка задаётся параметром `encoding`."
    ),
    responses={**NOT_FOUND, 422: {"description": "Нельзя прочитать как текст"}},
)
async def preview_import_file(
    name: str,
    settings: Settings,
    encoding: str = Query(default="utf-8-sig", max_length=32),
    lines: int = Query(default=20, ge=1, le=200),
) -> FilePreview:
    target = _safe_path(settings.import_dir, name)
    if not target.is_file():
        raise HTTPException(404, "Файл не найден")
    try:
        codecs.lookup(encoding)
    except LookupError as error:
        raise HTTPException(422, f"Неизвестная кодировка {encoding!r}") from error
    if target.suffix.lower() == ".parquet":
        raise HTTPException(422, "Предпросмотр доступен только для текстовых файлов")
    head: list[str] = []
    try:
        with target.open("r", encoding=encoding, newline="") as handle:
            for line in handle:
                head.append(line.rstrip("\r\n")[:2000])
                if len(head) >= lines:
                    break
    except UnicodeDecodeError as error:
        raise HTTPException(
            422,
            f"Файл не читается в кодировке {encoding}: {error.reason}. "
            "Попробуйте cp1251.",
        ) from error
    return FilePreview(
        name=target.name,
        size=target.stat().st_size,
        encoding=encoding,
        lines=head,
        delimiter=_guess_delimiter(head),
    )


@router.delete(
    "/import-files/{name}",
    status_code=204,
    tags=["files"],
    operation_id="deleteImportFile",
    summary="Удалить загруженный файл",
    responses=NOT_FOUND,
)
async def delete_import_file(name: str, settings: Settings) -> None:
    target = _safe_path(settings.import_dir, name)
    if not target.is_file():
        raise HTTPException(404, "Файл не найден")
    target.unlink()


# --- пресеты -----------------------------------------------------------------


class PresetOut(BaseModel):
    name: str
    description: str | None
    builtin: bool
    mapping: FileMapping


class PresetIn(BaseModel):
    description: str | None = Field(default=None, max_length=500)
    mapping: FileMapping


@router.get(
    "/import-presets",
    response_model=list[PresetOut],
    tags=["files"],
    operation_id="listImportPresets",
    summary="Пресеты маппинга файлов",
    description="Встроенные первыми (`finam`, `finam_no_header`, `iso_utc`).",
)
async def list_import_presets(session: DbSession) -> list[PresetOut]:
    presets = await session.run_sync(lambda sync: list_presets(sync))
    return [
        PresetOut(
            name=p.name,
            description=p.description,
            builtin=p.builtin,
            mapping=FileMapping.model_validate(p.mapping),
        )
        for p in presets
    ]


@router.put(
    "/import-presets/{name}",
    response_model=PresetOut,
    tags=["files"],
    operation_id="saveImportPreset",
    summary="Создать или обновить пресет",
    responses={
        409: {"description": "Пресет встроенный"},
        422: {"description": "Недопустимое имя"},
    },
)
async def put_import_preset(name: str, body: PresetIn, session: DbSession) -> PresetOut:
    def save(sync: Any) -> Any:
        return save_preset(sync, name, body.mapping, description=body.description)

    try:
        preset = await session.run_sync(save)
    except ValueError as error:
        status = 409 if "встроенный" in str(error) else 422
        await session.rollback()
        raise HTTPException(status, str(error)) from error
    await session.commit()
    return PresetOut(
        name=preset.name,
        description=preset.description,
        builtin=preset.builtin,
        mapping=FileMapping.model_validate(preset.mapping),
    )


@router.delete(
    "/import-presets/{name}",
    status_code=204,
    tags=["files"],
    operation_id="deleteImportPreset",
    summary="Удалить пользовательский пресет",
    responses={**NOT_FOUND, 409: {"description": "Пресет встроенный"}},
)
async def remove_import_preset(name: str, session: DbSession) -> None:
    try:
        removed = await session.run_sync(lambda sync: delete_preset(sync, name))
    except ValueError as error:
        await session.rollback()
        raise HTTPException(409, str(error)) from error
    if not removed:
        raise HTTPException(404, "Пресет не найден")
    await session.commit()


# --- конфликты ---------------------------------------------------------------


class CandleValues(BaseModel):
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_count: int | None = None


class ConflictOut(BaseModel):
    id: int
    timestamp: datetime
    status: str = Field(description="pending, accepted, rejected")
    existing: CandleValues = Field(description="Активная свеча на момент обнаружения")
    incoming: CandleValues = Field(description="Свеча из импорта")
    resolved_at: datetime | None
    resolution_import_id: int | None


class ResolveIn(BaseModel):
    accept: bool = Field(
        description="`true` — принять новые значения, `false` — отклонить"
    )
    conflict_ids: list[int] | None = Field(
        default=None, description="Только эти конфликты; по умолчанию — все ожидающие"
    )


class ResolveOut(BaseModel):
    resolved: int
    resolution_import_id: int | None
    dataset_version_id: int | None = Field(
        description="Новая версия датасета (только при принятии изменений)"
    )
    aggregation_job_id: int | None = Field(description="Задача пересборки баров")


@router.get(
    "/imports/{import_id}/conflicts",
    response_model=list[ConflictOut],
    tags=["conflicts"],
    operation_id="listImportConflicts",
    summary="Конфликты импорта",
    description=(
        "Свечи из импорта, отличающиеся от уже загруженных. Не применяются без "
        "решения пользователя (ADR-0005)."
    ),
    responses=NOT_FOUND,
)
async def list_import_conflicts(
    import_id: int,
    session: DbSession,
    status: str | None = Query(
        default="pending", pattern="^(pending|accepted|rejected)$"
    ),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[ConflictOut]:
    await _find_import(session, import_id)
    query = (
        select(ImportConflict, RawCandle1m)
        .join(RawCandle1m, RawCandle1m.id == ImportConflict.existing_candle_id)
        .where(ImportConflict.data_import_id == import_id)
        .order_by(ImportConflict.timestamp)
        .limit(limit)
        .offset(offset)
    )
    if status is not None:
        query = query.where(ImportConflict.status == status)
    return [
        ConflictOut(
            id=conflict.id,
            timestamp=conflict.timestamp,
            status=conflict.status,
            existing=CandleValues(
                open=existing.open,
                high=existing.high,
                low=existing.low,
                close=existing.close,
                volume=existing.volume,
                trade_count=existing.trade_count,
            ),
            incoming=CandleValues(
                open=conflict.new_open,
                high=conflict.new_high,
                low=conflict.new_low,
                close=conflict.new_close,
                volume=conflict.new_volume,
                trade_count=conflict.new_trade_count,
            ),
            resolved_at=conflict.resolved_at,
            resolution_import_id=conflict.resolution_import_id,
        )
        for conflict, existing in await session.execute(query)
    ]


@router.post(
    "/imports/{import_id}/conflicts/resolve",
    response_model=ResolveOut,
    tags=["conflicts"],
    operation_id="resolveImportConflicts",
    summary="Принять или отклонить конфликты",
    description=(
        "Принятие создаёт импорт-разрешение и новую версию датасета (старые версии "
        "остаются воспроизводимыми) и ставит пересборку баров."
    ),
    responses=NOT_FOUND,
)
async def resolve_import_conflicts(
    import_id: int, body: ResolveIn, session: DbSession
) -> ResolveOut:
    await _find_import(session, import_id)
    pending = await session.scalar(
        select(func.count())
        .select_from(ImportConflict)
        .where(
            ImportConflict.data_import_id == import_id,
            ImportConflict.status == "pending",
            *(
                [ImportConflict.id.in_(body.conflict_ids)]
                if body.conflict_ids is not None
                else []
            ),
        )
    )

    def resolve(sync: Any) -> tuple[int | None, int | None, int | None]:
        resolution = resolve_conflicts(
            sync, import_id, accept=body.accept, conflict_ids=body.conflict_ids
        )
        if resolution is None:
            return None, None, None
        version = extend_dataset_version(sync, resolution)
        contract_id = sync.get(DataImport, resolution).contract_id
        return resolution, version.id, enqueue_aggregation(sync, contract_id)

    resolution, version_id, job_id = await session.run_sync(resolve)
    await session.commit()
    return ResolveOut(
        resolved=pending or 0,
        resolution_import_id=resolution,
        dataset_version_id=version_id,
        aggregation_job_id=job_id,
    )
