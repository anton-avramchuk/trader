# trader

Исследовательская торговая платформа для фьючерсов MOEX: данные, мульти-таймфреймные индикаторы, структура рынка, уровни, паттерны, статистика и бэктест.

- Требования и решения: [`docs/spec.md`](docs/spec.md)
- Глоссарий домена: [`CONTEXT.md`](CONTEXT.md)
- Архитектурные решения: [`docs/adr/`](docs/adr/)

## Структура

| Путь | Что |
|---|---|
| `apps/web` | Angular + Taiga UI |
| `apps/web-e2e` | Playwright e2e для `web` |
| `apps/api` | FastAPI (Python) |
| `apps/worker` | Фоновый worker (Python) |
| `libs/engine` | Аналитические движки (Python) |

Монорепо на Nx; Python-проекты подключены через `@nxlv/python` и управляются uv (у каждого свой `pyproject.toml` и `uv.lock`).

## Требования

- Node.js ≥ 22.22 (Angular 22)
- [uv](https://docs.astral.sh/uv/) — сам скачает Python 3.12
- Docker (для PostgreSQL, см. задачу про Docker Compose)

## Быстрый старт

```bash
npm install          # .npmrc включает legacy-peer-deps (см. ниже)
npx nx run-many -t lint typecheck test build --exclude=web-e2e

npx nx serve web     # http://localhost:4200
npx nx serve api     # http://127.0.0.1:8000/health
npx nx serve worker
```

`legacy-peer-deps=true` в `.npmrc` нужен из-за бага npm 11 (`Cannot read properties of null (reading 'edgesOut')`) при разборе peer-зависимостей vitest/jsdom.
