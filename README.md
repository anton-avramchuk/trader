# trader

Исследовательская торговая платформа: данные, мульти-таймфреймные индикаторы, структура рынка, уровни, паттерны, статистика и бэктест.

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
| `apps/importer` | Сервис истории: тикеры и готовые свечи (прокси к MOEX ISS, ADR-0028) |
| `libs/engine` | Аналитические движки (Python) |
| `libs/db` | Доступ к PostgreSQL: настройки, движки SQLAlchemy, миграции Alembic |

Монорепо на Nx; Python-проекты подключены через `@nxlv/python` и управляются uv (у каждого свой `pyproject.toml` и `uv.lock`).

## Требования

- Node.js ≥ 22.22 (Angular 22)
- [uv](https://docs.astral.sh/uv/) — сам скачает Python 3.12
- Docker с Compose

## Быстрый старт

```bash
npm install          # .npmrc включает legacy-peer-deps (см. ниже)
npx nx run-many -t lint typecheck test build --exclude=web-e2e

npx nx serve web     # http://localhost:4200
npx nx serve api     # http://127.0.0.1:8000/health
npx nx serve worker
```

`legacy-peer-deps=true` в `.npmrc` нужен из-за бага npm 11 (`Cannot read properties of null (reading 'edgesOut')`) при разборе peer-зависимостей vitest/jsdom.

## Docker Compose

```bash
cp .env.example .env        # необязательно: значения по умолчанию уже подходят
docker compose up -d --build
```


|---|---|
| PostgreSQL 18 | `127.0.0.1:5432` (данные в томе `pgdata`) |
| pgAdmin | http://127.0.0.1:5050 — без входа, сервер «trader» уже добавлен и подключается без пароля |
| API | http://127.0.0.1:8000/health |
| Web (dev-сервер) | http://127.0.0.1:4200 |
| worker | без порта, статус в `docker compose logs worker` |

Все порты слушают только localhost. Миграции Alembic применяются при старте `api`.

Интеграционные тесты (`libs/db`, `apps/api`) выполняются, если задан `TRADER_DATABASE_URL`, иначе пропускаются:

```bash
TRADER_DATABASE_URL=postgresql+psycopg://trader:trader@127.0.0.1:5432/trader npx nx run-many -t test -p db api
```

## Очередь задач

Долгие операции выполняет worker; API только ставит задачи и показывает прогресс (ADR-0002).

```bash
# поставить демо-задачу (встроенный обработчик demo.sleep)
curl -X POST http://127.0.0.1:8000/jobs -H 'content-type: application/json'   -d '{"type": "demo.sleep", "params": {"seconds": 5, "steps": 10}}'

curl http://127.0.0.1:8000/jobs/1          # состояние
curl -X POST http://127.0.0.1:8000/jobs/1/cancel
# WebSocket с прогрессом: ws://127.0.0.1:8000/ws/jobs/1
```

Новый обработчик регистрируется в `apps/worker/src/trader_worker/handlers.py` (`default_registry`) и должен быть идемпотентным: задача потерянного worker'а выполняется заново.

## Данные: importer и инструменты

Платформа работает с абстрактными инструментами и готовыми свечами (ADR-0028): ни контрактов, ни склейки, ни роллов. Историю отдаёт отдельный сервис `apps/importer` (`GET /tickers`, `/history`, `/health`; в Compose — `importer`, адрес для API и worker — `TRADER_IMPORTER_URL`).

1. В UI (Data → Instruments) или `POST /instruments {"ticker": "SBER"}` заводится инструмент: название, валюта, шаг цены и часовой пояс берутся у importer; стоимость тика (`tick_value`) задаётся вручную — только для денег в бэктесте.
2. Свечи подгружаются за период задачей `candles.load` (`POST /instruments/{id}/load`); повторная загрузка перезаписывает свечи, журнал — `GET /instruments/{id}/loads`.

```bash
curl -X POST http://127.0.0.1:8000/instruments -H 'content-type: application/json' -d '{"ticker": "SBER"}'
curl -X POST http://127.0.0.1:8000/instruments/1/load -H 'content-type: application/json' -d '{"period_from": "2025-01-01", "period_to": "2026-01-01"}'
```

## Что умеет API

Инструменты: `GET/POST /instruments`, `PATCH /instruments/{id}`, `GET /importer/tickers`, `POST /instruments/{id}/load`, `GET /instruments/{id}/loads`. Полный список — в Swagger.

Свечи: `GET /candles` (`instrument_id`, TF `15m/1h/4h/1d/1w`, диапазон, `limit` + `next_start`, `tail` для подгрузки истории) и `GET /snapshot?as_of=…` — только свечи, закрытые к моменту.

TS-клиент — `libs/api-client` (`@trader/api-client`, типы генерируются из OpenAPI: `npx nx run api-client:generate`; тест API падает, если закоммиченная схема устарела).

## Веб-интерфейс

http://127.0.0.1:4200 (Angular + Taiga UI). Dev-сервер проксирует `/api` (REST и WebSocket) на API, адрес — переменная `API_URL` (в Compose `http://api:8000`, локально по умолчанию `http://127.0.0.1:8000`). Разделы: Data (Instruments), Chart, Replay, Research, Backtest. Время в UI — МСК.

## Индикаторы и профили графика

Индикаторы — плагины (ADR-0020): `GET /indicators` (каталог с JSON-схемой параметров), `GET /indicator-values` (chart TF, source TF, `as_of`; старший TF проецируется ступенькой, без формирующегося бара). На графике и в Replay: «Индикатор» → тип, параметры, source TF. Профили (`/chart-profiles`) — глобальные или для инструмента; последний применённый открывается сам.

Движки структуры (swing, уровни, паттерны) — плагины с неизменяемым логом событий (ADR-0021): `GET /engines`, `GET /engine-runs`, `GET /engine-runs/{id}/events` (`as_of`, `view=history|current`); прогон запускает задача `engine.run` и продолжается на новых барах, пока история не менялась.

## Документация API (Swagger)

При запущенном стеке: Swagger UI — http://127.0.0.1:8000/docs, ReDoc — http://127.0.0.1:8000/redoc, схема — http://127.0.0.1:8000/openapi.json. У операций стабильные `operationId` (`createJob`, `getJob`…) — по схеме генерируется клиент для UI. WebSocket `/ws/jobs/{id}` в OpenAPI не входит и описан в тексте схемы.
