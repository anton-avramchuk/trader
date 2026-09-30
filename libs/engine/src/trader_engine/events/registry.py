"""Реестр движков событий: регистрация, создание по имени и параметрам."""

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ValidationError

from trader_engine.events.base import EventEngine

_REGISTRY: dict[str, type[EventEngine]] = {}


def register[T: type[EventEngine]](cls: T) -> T:
    """Декоратор: ``@register class ZigZag(EventEngine): ...``."""
    if cls.name in _REGISTRY:
        raise ValueError(f"Движок {cls.name!r} уже зарегистрирован")
    _REGISTRY[cls.name] = cls
    return cls


def unregister(name: str) -> None:
    _REGISTRY.pop(name, None)


def available() -> list[type[EventEngine]]:
    return [_REGISTRY[name] for name in sorted(_REGISTRY)]


def get(name: str) -> type[EventEngine]:
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "нет"
        raise KeyError(f"Неизвестный движок {name!r}; доступны: {known}") from None


def create(name: str, params: dict[str, Any] | None = None) -> EventEngine:
    """Движок ``name`` с проверенными параметрами (``ValueError`` при ошибке)."""
    cls = get(name)
    try:
        validated = cls.Params.model_validate(params or {})
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors()
        )
        raise ValueError(f"Некорректные параметры {name}: {problems}") from error
    return cls(validated)


def hash_params(params: BaseModel) -> str:
    """Стабильный отпечаток параметров для ключа прогона."""
    payload = json.dumps(params.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def describe(cls: type[EventEngine]) -> dict[str, Any]:
    defaults = cls.Params()
    return {
        "name": cls.name,
        "title": cls.title,
        "version": cls.version,
        "params_schema": cls.Params.model_json_schema(),
        "defaults": defaults.model_dump(mode="json"),
    }
