"""Реестр индикаторов: регистрация плагинов, создание по имени и параметрам."""

import hashlib
import json
from typing import Any

from pydantic import ValidationError

from trader_engine.indicators.base import Indicator

_REGISTRY: dict[str, type[Indicator]] = {}


def register[T: type[Indicator]](cls: T) -> T:
    """Декоратор плагина: ``@register class Sma(Indicator): ...``."""
    if cls.name in _REGISTRY:
        raise ValueError(f"Индикатор {cls.name!r} уже зарегистрирован")
    _REGISTRY[cls.name] = cls
    return cls


def unregister(name: str) -> None:
    """Снять плагин с регистрации (для тестов и горячей замены)."""
    _REGISTRY.pop(name, None)


def available() -> list[type[Indicator]]:
    """Все плагины по имени."""
    return [_REGISTRY[name] for name in sorted(_REGISTRY)]


def get(name: str) -> type[Indicator]:
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "нет"
        raise KeyError(f"Неизвестный индикатор {name!r}; доступны: {known}") from None


def create(name: str, params: dict[str, Any] | None = None) -> Indicator:
    """Индикатор ``name`` с проверенными параметрами (``ValueError`` при ошибке)."""
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


def params_hash(indicator: Indicator) -> str:
    """Стабильный отпечаток параметров (для ключей кэша и версионирования)."""
    payload = json.dumps(
        indicator.params.model_dump(mode="json"), sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def describe(cls: type[Indicator]) -> dict[str, Any]:
    """Описание плагина для UI: метаданные, JSON-схема и значения по умолчанию."""
    defaults = cls.Params()
    return {
        "name": cls.name,
        "title": cls.title,
        "version": cls.version,
        "pane": cls.pane,
        "outputs": list(cls.outputs),
        "warmup_bars": cls(defaults).warmup_bars,
        "params_schema": cls.Params.model_json_schema(),
        "defaults": defaults.model_dump(mode="json"),
    }
