# 0001. Монорепо Nx: Angular + Taiga UI + Python

**Статус:** принято

## Контекст
UI обязателен, бэкенд — Python (численные расчёты). Нужен единый граф сборки, кэш и affected-запуски.

## Решение
Один Nx workspace. Frontend — Angular (standalone, signals) + **только Taiga UI**. Python-проекты (API, worker, engine) — через `@nxlv/python` + uv. TS-клиент API генерируется из OpenAPI FastAPI в Nx-библиотеку. Инструменты: ruff, pyright (strict для engine), pytest + hypothesis; Vitest/Jest, Playwright.

## Последствия
- Зависимость от стороннего Nx-плагина для Python; при проблемах Python-проекты можно вести как обычные uv-пакеты с Nx run-commands.
- Контракт API проверяется на уровне типов фронтенда.
