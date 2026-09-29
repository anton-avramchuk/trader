"""Пресеты файлового импорта: чтение, сохранение, удаление."""

from sqlalchemy import select
from sqlalchemy.orm import Session
from trader_engine.file_import import FileMapping

from trader_db.models import ImportPreset

MAX_NAME_LENGTH = 64


def _clean_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or len(cleaned) > MAX_NAME_LENGTH:
        raise ValueError(f"Имя пресета — от 1 до {MAX_NAME_LENGTH} символов")
    return cleaned


def _find(session: Session, name: str) -> ImportPreset | None:
    return session.scalars(
        select(ImportPreset).where(ImportPreset.name == name)
    ).one_or_none()


def list_presets(session: Session) -> list[ImportPreset]:
    """Встроенные пресеты первыми, затем пользовательские по имени."""
    return list(
        session.scalars(
            select(ImportPreset).order_by(
                ImportPreset.builtin.desc(), ImportPreset.name
            )
        )
    )


def get_preset(session: Session, name: str) -> FileMapping:
    """Маппинг пресета; ``LookupError``, если пресета нет."""
    preset = _find(session, name)
    if preset is None:
        known = ", ".join(repr(p.name) for p in list_presets(session))
        raise LookupError(f"Пресет {name!r} не найден; доступные: {known}")
    return FileMapping.model_validate(preset.mapping)


def save_preset(
    session: Session,
    name: str,
    mapping: FileMapping,
    *,
    description: str | None = None,
) -> ImportPreset:
    """Создать или обновить пользовательский пресет; встроенные менять нельзя."""
    name = _clean_name(name)
    preset = _find(session, name)
    if preset is not None and preset.builtin:
        raise ValueError(f"Пресет {name!r} встроенный и не изменяется")
    payload = mapping.model_dump(mode="json")
    if preset is None:
        preset = ImportPreset(name=name, description=description, mapping=payload)
        session.add(preset)
    else:
        preset.mapping = payload
        preset.description = description
    session.flush()
    return preset


def delete_preset(session: Session, name: str) -> bool:
    """Удалить пользовательский пресет; ``False``, если такого нет."""
    preset = _find(session, name)
    if preset is None:
        return False
    if preset.builtin:
        raise ValueError(f"Пресет {name!r} встроенный и не удаляется")
    session.delete(preset)
    session.flush()
    return True
