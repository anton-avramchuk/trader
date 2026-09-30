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

## Импорт файлов

Файл кладётся в каталог импорта (`TRADER_IMPORT_DIR`; в Compose — том `imports`), затем ставится задача `import.file`:

```bash
docker compose cp finam.csv worker:/data/imports/finam.csv

curl -X POST http://127.0.0.1:8000/jobs -H 'content-type: application/json' -d '{
  "type": "import.file",
  "params": {"contract_id": 1, "file": "finam.csv", "preset": "finam"}
}'
```

Встроенные пресеты: `finam`, `finam_no_header`, `iso_utc`. Свой формат — `mapping` вместо `preset`, поправка пресета — `overrides` (например, `{"timezone": "UTC", "encoding": "cp1251"}`). Форматы: CSV, JSON (массив или NDJSON), Parquet. Отчёт (вставлено, дубликаты, конфликты, ошибки строк, пропуски, диапазон, min/max цена) — в результате задачи. Подробности — ADR-0015 и ADR-0016.

## Continuous-серия

Ролл происходит в начале торговой недели за N торговых дней до экспирации, склейка ratio (ADR-0019). События `roll_events` пересчитываются в конце задачи `aggregate.contract`; склеенные бары строятся при чтении (`trader_db.read_continuous`).

## Что умеет API

Справочники: `/roots`, `/roots/{id}/contracts`, `/calendars`. Контракты ISS: `POST /roots/{id}/iss-preview` (задача `dry_run`, результат — список для подтверждения), затем `POST /roots/{id}/contracts/from-iss`. Импорты: `POST /contracts/{id}/imports/iss|file`, файлы — `PUT /import-files/{name}` (тело — содержимое файла), пресеты — `/import-presets`, отчёты — `/imports`, конфликты — `/imports/{id}/conflicts` и `.../resolve`. Полный список — в Swagger.

Свечи: `GET /candles` (контракт или continuous по `root_id`, TF `1m/15m/1h/4h/1d/1w`, диапазон, `limit` + `next_start`) и `GET /snapshot?as_of=…` — только бары, закрытые к моменту, и только известные к нему роллы (масштаб такой, каким его видел бы наблюдатель тогда).

TS-клиент — `libs/api-client` (`@trader/api-client`, типы генерируются из OpenAPI: `npx nx run api-client:generate`; тест API падает, если закоммиченная схема устарела).

## Регрессионный датасет

`tests/fixtures/ng_2026_03.csv.gz` — реальные минуты NGH6/NGJ6 с ISS (09–31.03.2026: ролл и смена режима сессий 23.03). Тест `apps/api/tests/test_regression_dataset.py` прогоняет импорт → бары → ролл → API и сверяет с `ng_2026_03.expected.json`. Осознанное обновление: `UPDATE_REGRESSION=1 pytest tests/test_regression_dataset.py`; перезагрузка данных — `scripts/build_regression_fixture.py`.

## Веб-интерфейс

http://127.0.0.1:4200 (Angular + Taiga UI). Dev-сервер проксирует `/api` (REST и WebSocket) на API, адрес — переменная `API_URL` (в Compose `http://api:8000`, локально по умолчанию `http://127.0.0.1:8000`). Разделы: Data (Instruments, Import, Quality), Chart, Research, Backtest. Время в UI — МСК.

## Стоимость шага цены

Выводится из дневных итогов ISS (ADR-0007) задачей `iss.step_prices` (раз в сутки по расписанию `iss-step-prices-daily`; вручную — кнопка «Обновить шаг» в Instruments или `POST /contracts/{id}/step-prices/refresh`). История — `GET /contracts/{id}/step-prices`, последняя — в карточке контракта.

## Документация API (Swagger)

При запущенном стеке: Swagger UI — http://127.0.0.1:8000/docs, ReDoc — http://127.0.0.1:8000/redoc, схема — http://127.0.0.1:8000/openapi.json. У операций стабильные `operationId` (`createJob`, `getJob`…) — по схеме генерируется клиент для UI. WebSocket `/ws/jobs/{id}` в OpenAPI не входит и описан в тексте схемы.

## Загрузка истории с MOEX ISS

Root (например, `NG`) заводится в БД; его `code` — код базового актива ISS. Затем:

```bash
# найти контракты 2020–2021 и поставить загрузку истории по каждому
curl -X POST http://127.0.0.1:8000/jobs -H 'content-type: application/json'   -d '{"type": "iss.sync_root", "params": {"root_id": 2, "from_year": 2020}}'

# история одного контракта за период (без from/till — вся доступная)
curl -X POST http://127.0.0.1:8000/jobs -H 'content-type: application/json'   -d '{"type": "import.iss", "params": {"contract_id": 12, "from": "2020-11-01", "till": "2020-11-30"}}'
```

Загрузка возобновляемая: повторный запуск догружает только недостающее; сегодняшний день не грузится. Подробности — ADR-0017. Проверка на настоящем ISS: `TRADER_LIVE_TESTS=1 uv run pytest tests/test_iss_live.py` в `libs/providers`.

## Бары 15m / 1h / 4h / 1d / 1w

Бары строятся из минутных свечей по торговому календарю (ADR-0018). Импорт, добавивший свечи, сам ставит задачу `aggregate.contract`; вручную:

```bash
curl -X POST http://127.0.0.1:8000/jobs -H 'content-type: application/json'   -d '{"type": "aggregate.contract", "params": {"contract_id": 12, "timeframes": ["1d"], "force": true}}'
```

Пересборка инкрементальная (от торговой недели с новыми данными); формирующийся бар не публикуется.

