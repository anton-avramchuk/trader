"""Dataset versions: неизменяемые манифесты и чтение свечей версии (ADR-0005).

Свеча входит в версию, если её импорт есть в манифесте и она не замещена импортом
из того же манифеста. Поэтому версия, созданная до принятия конфликта, и после
него возвращает те же данные, а новая версия с импортом-разрешением — исправленные.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.orm import Session

from trader_db.imports import Candle1m
from trader_db.models import (
    DataImport,
    DatasetVersion,
    DatasetVersionImport,
    RawCandle1m,
)


def _visible(contract_id: int, manifest: Sequence[int]) -> list[ColumnElement[bool]]:
    return [
        RawCandle1m.contract_id == contract_id,
        RawCandle1m.data_import_id.in_(manifest),
        or_(
            RawCandle1m.superseded_by_import_id.is_(None),
            RawCandle1m.superseded_by_import_id.not_in(manifest),
        ),
    ]


def visible_filter(
    contract_id: int, manifest: Sequence[int]
) -> list[ColumnElement[bool]]:
    """Условия WHERE для свечей, видимых в версии с манифестом ``manifest``."""
    return _visible(contract_id, manifest)


def manifest_of(session: Session, dataset_version_id: int) -> list[int]:
    return sorted(
        session.scalars(
            select(DatasetVersionImport.data_import_id).where(
                DatasetVersionImport.dataset_version_id == dataset_version_id
            )
        )
    )


def latest_dataset_version(
    session: Session, contract_id: int, timeframe_code: str = "1m"
) -> DatasetVersion | None:
    return session.scalars(
        select(DatasetVersion)
        .where(
            DatasetVersion.contract_id == contract_id,
            DatasetVersion.timeframe_code == timeframe_code,
        )
        .order_by(DatasetVersion.version_number.desc())
        .limit(1)
    ).one_or_none()


def create_dataset_version(
    session: Session,
    contract_id: int,
    timeframe_code: str,
    import_ids: Sequence[int],
    *,
    created_by_import_id: int | None = None,
) -> DatasetVersion:
    """Создать версию с манифестом ``import_ids``; диапазон и число строк — по факту."""
    manifest = sorted(set(import_ids))
    if not manifest:
        raise ValueError("Манифест не может быть пустым")
    imports = session.scalars(
        select(DataImport).where(DataImport.id.in_(manifest))
    ).all()
    if len(imports) != len(manifest):
        raise LookupError("В манифесте есть несуществующие импорты")
    for data_import in imports:
        if (
            data_import.contract_id != contract_id
            or data_import.timeframe_code != timeframe_code
        ):
            raise ValueError(
                f"Импорт {data_import.id} относится к другому контракту или TF"
            )
        if data_import.status != "completed":
            raise ValueError(
                f"Импорт {data_import.id} не завершён успешно ({data_import.status})"
            )

    rows, distinct_rows, start, end = session.execute(
        select(
            func.count(),
            func.count(func.distinct(RawCandle1m.timestamp)),
            func.min(RawCandle1m.timestamp),
            func.max(RawCandle1m.timestamp),
        ).where(and_(*_visible(contract_id, manifest)))
    ).one()
    if rows != distinct_rows:
        raise ValueError(
            "Манифест неконсистентен: для одной свечи видно несколько строк "
            "(в манифесте пропущен импорт-разрешение или промежуточный импорт)"
        )

    next_number = (
        session.scalar(
            select(func.coalesce(func.max(DatasetVersion.version_number), 0)).where(
                DatasetVersion.contract_id == contract_id,
                DatasetVersion.timeframe_code == timeframe_code,
            )
        )
        or 0
    ) + 1
    version = DatasetVersion(
        contract_id=contract_id,
        timeframe_code=timeframe_code,
        version_number=next_number,
        range_start=start,
        range_end=end,
        row_count=rows,
        created_by_import_id=created_by_import_id,
    )
    session.add(version)
    session.flush()
    session.add_all(
        DatasetVersionImport(dataset_version_id=version.id, data_import_id=import_id)
        for import_id in manifest
    )
    session.flush()
    return version


def extend_dataset_version(session: Session, import_id: int) -> DatasetVersion:
    """Версия «последняя + этот импорт»; если импорт уже в последней — она же."""
    data_import = session.get(DataImport, import_id)
    if data_import is None:
        raise LookupError(f"Импорт {import_id} не найден")
    latest = latest_dataset_version(
        session, data_import.contract_id, data_import.timeframe_code
    )
    previous = manifest_of(session, latest.id) if latest is not None else []
    if latest is not None and import_id in previous:
        return latest
    return create_dataset_version(
        session,
        data_import.contract_id,
        data_import.timeframe_code,
        [*previous, import_id],
        created_by_import_id=import_id,
    )


def read_candles(
    session: Session,
    dataset_version_id: int,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int | None = None,
    tail: bool = False,
) -> list[Candle1m]:
    """Свечи версии по возрастанию времени; ``[start, end)`` — необязательный фильтр.

    ``limit`` ограничивает число свечей: с начала диапазона, при ``tail`` — последние.
    """
    version = session.get(DatasetVersion, dataset_version_id)
    if version is None:
        raise LookupError(f"Dataset version {dataset_version_id} не найдена")
    query = select(RawCandle1m).where(
        *_visible(version.contract_id, manifest_of(session, dataset_version_id))
    )
    if start is not None:
        query = query.where(RawCandle1m.timestamp >= start)
    if end is not None:
        query = query.where(RawCandle1m.timestamp < end)
    query = query.order_by(
        RawCandle1m.timestamp.desc() if tail else RawCandle1m.timestamp
    )
    if limit is not None:
        query = query.limit(limit)
    rows = list(session.scalars(query))
    if tail:
        rows.reverse()
    return [
        Candle1m(
            timestamp=row.timestamp,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
            trade_count=row.trade_count,
            quote_volume=row.quote_volume,
        )
        for row in rows
    ]
