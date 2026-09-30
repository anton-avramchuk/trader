# 0021. Лог событий движков: прогоны, ревизии, продолжение, as-of

**Статус:** принято

Реализует [ADR-0003](0003-causal-event-log.md) и [ADR-0010](0010-derived-persistence.md) для движков структуры (swing, уровни, Fibonacci, pivot, паттерны).

## Движок (`trader_engine.events`)
- Подкласс `EventEngine` с метаданными (`name`, `title`, `version`), `Params` (Pydantic, значения по умолчанию) и каузальной машиной `on_bar(bar)`; события выпускаются через `emit(kind, status, payload, …)`. Регистрация `@register`, как у индикаторов.
- Событие (`Event`): `seq` (0, 1, 2 … в прогоне), `kind`, `status` (`detected` / `confirmed` / `revised` / `invalidated`), `payload` (JSON), `detected_at`, `confirmed_at`, `available_at`, `revises` (`seq` предыдущего события цепочки).
- **`available_at` ставит базовый класс** — закрытие бара, на котором событие выпущено. Движок не может выпустить событие «из будущего» (`detected_at`/`confirmed_at` позже бара — ошибка), не может ссылаться на несуществующее событие; `revised`/`invalidated` обязаны иметь `revises`, `detected` — не могут. Бары строго возрастают по `close_time`.
- Состояние движка — JSON (`get_state`/`set_state`); базовый класс добавляет счётчик `seq` и последний бар (`dump_state`/`load_state`). Продолжение с сохранённого состояния даёт те же события, что прогон с нуля.

## Хранение (миграция 0011)
- `engine_events` — **неизменяемая** таблица: триггер `forbid_modification` запрещает UPDATE и DELETE. Пересмотр — новая строка. Ключ `(run_id, seq)`, ссылка на пересматриваемое событие — составной FK `(run_id, revises_seq)` и `revises_seq < seq`. CHECK: допустимые статусы, соответствие `status` ↔ `revises_seq`, `detected_at ≤ confirmed_at ≤ available_at`.
- `engine_runs` — прогон: `engine`, `algorithm_version`, `params` и `params_hash`, ряд (ровно один из `contract_id` / `root_id` continuous) и `timeframe_code`, `dataset_version_id` (версия минутных данных контракта; у continuous пусто), а также служебные `bars_processed`, `last_close_time`, `input_fingerprint`, `state`. Строка прогона обновляется только при продолжении; на события это не влияет.
- `dataset_version_id` хранится и на событии: у продолжений одного прогона версии данных могут различаться.

## Прогон и продолжение (`trader_db.advance_run`, задача `engine.run`)
- Ключ прогона: движок + версия алгоритма + отпечаток параметров + ряд + TF. Смена версии алгоритма или параметров — другой прогон.
- Вход — полный ряд баров. `input_fingerprint` — **цепочный** sha256 по всем обработанным барам (время, OHLCV). Если отпечаток обработанной части совпадает, прогон **продолжается** с сохранённого состояния на новых барах (или `unchanged`); иначе — **новый прогон** с причиной (`ранее обработанные бары изменились`, `сетка баров изменилась`, `ряд стал короче…`), старые события остаются нетронутыми. Так безопасно обрабатываются правки данных и новый ролл continuous (масштаб прошлых баров меняется).
- Режим `full` всегда создаёт новый прогон. Строка последнего прогона блокируется `FOR UPDATE` на время продолжения.

## Выборка as-of (`trader_engine.events.current_events`, API)
- `known_at(events, t)` — события с `available_at ≤ t`.
- `current_events(events, t)` — актуальная картина: последняя версия каждой цепочки, отменённые (`invalidated`) исключены. Порядок — по началу цепочки.
- API: `GET /engines`, `GET /engine-runs`, `GET /engine-runs/{id}`, `GET /engine-runs/{id}/events?as_of=&kind=&view=history|current&after_seq=`. Запуск — `POST /jobs` с типом `engine.run` (`engine`, `engine_params`, `root_id` | `contract_id`, `timeframe`, `mode`).

## Последствия
- Каждый движок MVP-3+ проверяется теми же свойствами: префикс-инвариантность событий и совпадение продолжения с прогоном с нуля (в тестах каркаса они проверены на учебном движке; для реальных движков — в #41).
- Реальные движки (#34 и далее) добавляются плагинами без изменения хранилища.
