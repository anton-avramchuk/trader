# Исследовательская торговая платформа — ТЗ

> **Как читать.** Раздел «Решения, уточняющие ТЗ» перекрывает исходный текст ниже.
> Обоснования ключевых решений — в [`docs/adr/`](adr/), термины — в [`CONTEXT.md`](../CONTEXT.md).

## Решения, уточняющие ТЗ

### Стек и инфраструктура
- Единый **Nx workspace**: Angular + **Taiga UI** (без других UI-китов), Python через `@nxlv/python` + uv. React не используется.
- Python 3.12: FastAPI, Pydantic, SQLAlchemy 2 (sync в worker, async в API), Alembic, NumPy, Polars; ruff, pyright (strict для engine), pytest + hypothesis.
- Графики: **TradingView Lightweight Charts v5** (свечи, оверлеи через primitives/custom series) + **ECharts** (`ngx-echarts`) для статистики. Plotly не используется.
- TS-клиент API генерируется из OpenAPI FastAPI в Nx-библиотеку.
- PostgreSQL 16, всё поднимается через **Docker Compose**. Однопользовательский режим, без auth до MVP-10.
- Долгие операции — **очередь задач на PostgreSQL** (`FOR UPDATE SKIP LOCKED`) + отдельный worker; прогресс в UI через WebSocket.

### Рынок и инструменты
- Первые инструменты — **фьючерсы MOEX FORTS** (газ, нефть, золото). Конкретные тикеры пользователь заводит сам в UI.
- Модель: **Root** (базовый актив) → **Contract** (серия с экспирацией) → **Continuous series** (derived).
- Ключ контракта — `(root, expiration_date)`; SECID и коды провайдеров — атрибуты (SECID повторяется раз в 10 лет).
- Контракты подтягиваются из ISS по коду базового актива (включая истёкшие), с возможностью ручной правки.
- Ролл: за N торговых дней до экспирации, **выровнен по началу торговой недели** (все TF переключаются одновременно). Склейка **ratio**. `roll_events` хранятся явно с `available_at`.
- Движки работают по continuous-серии; бэктест исполняет сделки в реальном фронтовом контракте, включая издержки ролла.
- Дневная история `step_price` (стоимость шага в ₽) хранится по контракту; PnL бэктеста — в USD и в ₽ по историческому `step_price`.

### Данные
- Источник истории — **MOEX ISS, свечи 1m** (архивный raw; истёкшие контракты доступны примерно с 2020 г.).
- Из 1m по торговому календарю собираются **отдельно хранящиеся TF: 15m, 1h, 4h, 1d, 1w** (`provenance = aggregated(iss_1m)`). 1m в UI не показывается.
- **T-Invest** — live-данные, paper и live торговля; его свечи используются для сверки. Свечи текущего дня из T-Invest замещаются свечами ISS ночной задачей (bitemporal).
- Finam и другие источники — через CSV-мастер (сопоставление колонок, пресеты, явный часовой пояс; по умолчанию Europe/Moscow).
- Raw-данные **bitemporal и неизменяемы**: `data_import_id`, `superseded_by_import_id`. Конфликтующие значения не применяются автоматически — пользователь подтверждает их в Import UI.
- **Dataset version** — неизменяемый манифест (инструмент + TF + диапазон + набор импортов), создаётся автоматически при каждом принятом импорте. UI работает с последней версией, experiment/backtest закрепляют свою.
- Цены: `NUMERIC(18,8)` в raw, `double precision` в derived; движки считают во float64; сравнения — с допуском в долях tick size.

### Торговый календарь
- `TradingCalendar` у root: **правила с датами действия** (сессии, принадлежность вечерней сессии торговому дню, клиринги, выходные сессии) + таблица праздников.
- Правила: «до 23.03.2026» (вечерняя сессия относится к следующему торговому дню) и «с 23.03.2026» (единая торговая сессия 07:00–23:50 МСК, вечерняя относится к текущему дню).
- 1d/1w — по торговому дню/неделе биржи. Intraday TF выравниваются от начала торгового дня; неполные бары на краях сессии допустимы и помечаются флагом.
- Выходные сессии хранятся в raw, но **по умолчанию исключаются** из continuous-серии (флаг root `include_weekend_sessions`).
- Разрывы по расписанию (ночь, выходные, клиринг) не считаются missing intervals.
- Хранение в UTC, отображение в часовом поясе биржи (МСК).

### Временная модель
- `timestamp` свечи = **open time**. `close_time` **хранится явно** (с учётом обрезки сессией). `available_at` свечи = `close_time`.
- Формирующиеся бары в расчёты не попадают никогда (в live только отображаются).
- Все алгоритмы — **каузальные инкрементальные** (`update(candle) -> events`); batch = прогон по истории. Результат — **неизменяемый лог событий** с `available_at`; пересмотр (ZigZag, отмена паттерна) — новое событие `revised`/`invalidated`.
- Для каждого алгоритма — тест префикс-инвариантности: `f(data[:t])[-1] == f(data)[t]`.
- `event_outcomes` имеют собственный `available_at` (outcome на горизонте n известен через n баров); forecast as-of `t` использует только созревшие outcomes.

### Multi-timeframe
- Source TF задаётся у каждого индикатора/уровня в UI; расчёт — **по свечам source TF**.
- **Старший TF на младшем графике** — ступенькой, значение меняется в `available_at` старшего бара; формирующийся старший бар не участвует.
- **Младший TF на старшем графике запрещён.**

### Replay
- **Visual replay**: курсор на клиенте, сервер отдаёт `snapshot?as_of=t` (свечи, события с `available_at <= t`, forecast as-of).
- **Online replay**: сервер заново прогоняет state machines свеча за свечой и сравнивает с сохранённым логом (pytest-сьют + кнопка «Verify» в UI).

### Движки MVP-2/3
- Индикаторы **не персистятся**: считаются на лету, LRU-кэш по `(dataset_version, indicator, version, params, source_tf)`. У каждого — `warmup_bars`; до конца прогрева значения не показываются и не участвуют в расчётах.
- Персистятся события: swing, levels, fib/pivot-структуры, паттерны, features, outcomes, DTW-матчи — с `algorithm_version`, `params_hash`, `dataset_version_id`.
- Swing по умолчанию — **ATR-ZigZag (1.5 × ATR14 того же TF)**; подтверждение — закрытием бара на расстоянии ≥ порога, `confirmed_at = close_time` этого бара. Вторым — fixed-window.
- Fibonacci: одна автоматическая сетка на source TF (последний подтверждённый swing → экстремум текущего колена), новая сетка — новое событие. Ручные сетки сохраняются, но в статистике не участвуют.
- Уровни: touch = заход в зону ± k·ATR с отбоем ≥ m·ATR без закрытия за зоной (старт k=0.25, m=0.5); закрытие за зоной — пробой. Состояния: `active` → `broken` (возможна смена роли support↔resistance) / `expired` (нет касаний дольше `max_age`). Каждое изменение — событие.
- Strength — **вектор компонентов** + агрегат v1 со взвешенной суммой; веса — вручную, в конфиге, версионируются. Подбор весов по outcomes — только отдельным experiment.
- Кластеризация зон — представление в контексте графика, порог в ATR **TF графика**; для Research TF задаётся в параметрах запроса.
- Профили графика — в PostgreSQL, глобальные или привязанные к root.
- На графике открывается continuous-серия (роллы отмечены маркерами) или конкретный контракт в своих ценах.

### Статистика, outcomes, бэктест
- Горизонты outcomes — в барах торгового времени, с флагами `crosses_session_gap`, `crosses_weekend`, `crosses_roll`.
- Research: de-overlap событий и эффективный размер выборки, CI через block bootstrap, baseline «случайная точка в том же режиме» (MVP-5); журнал запросов и заблокированный test-период (MVP-8).
- Неоднозначный бар (стоп и тейк в одной свече): пессимистичное допущение + флаг `ambiguous_bar`, отдельная строка в отчёте.

### Этапы
- **MVP-1a** — один root, ISS/CSV 1m, календарь, continuous, отдельно хранящиеся TF, график, visual replay.
- **MVP-1b** — T-Invest (сначала проверить историю экспирированных контрактов), сверка, конфликты, качество данных, live.
- **MVP-2**, **MVP-3** — как ниже, с учётом решений выше.
- Перед MVP-4…10 — отдельный раунд проектирования.

---

## Исходное ТЗ

# 1. Назначение системы

Разработать исследовательскую торговую платформу для анализа ограниченного набора финансовых инструментов, автоматического выявления технических формаций и рыночных уровней, расчёта индикаторов на нескольких таймфреймах и статистической оценки последующего поведения цены.

Первая версия системы рассчитана на **3–4 выбранных инструмента**. Количество инструментов должно конфигурироваться, но архитектура не должна быть ориентирована на массовый скрининг тысяч инструментов.

Система должна объединять:

```text
Historical Data
      ↓
Indicators
      ↓
Market Structure
      ↓
Support / Resistance / Fibonacci / Pivots
      ↓
Pattern Recognition
      ↓
Historical Analogue Search
      ↓
Statistical Analysis
      ↓
Forecast
      ↓
Backtest / Paper Trading / Live Trading
```

Основная цель — не построить «индикатор BUY/SELL», а создать среду, в которой можно исследовать гипотезы и проверять их на истории.

# 2. Основные задачи

Система должна:

1. Хранить исторические рыночные данные в PostgreSQL.
2. Импортировать данные из внешних источников.
3. Получать новые данные через API/WebSocket поставщиков.
4. Работать с несколькими таймфреймами.
5. Позволять отображать данные одного таймфрейма на графике другого.
6. Рассчитывать технические индикаторы.
7. Определять swing points и рыночную структуру.
8. Строить support/resistance.
9. Строить Fibonacci levels.
10. Рассчитывать Pivot Points.
11. Искать зоны confluence нескольких уровней.
12. Распознавать технические паттерны.
13. Искать исторически похожие ситуации.
14. Рассчитывать фактическое последующее поведение цены после события.
15. Строить статистический forecast.
16. Выполнять backtesting.
17. Выполнять walk-forward testing.
18. Поддерживать paper trading.
19. В дальнейшем поддерживать live trading.
20. Предоставлять web UI для визуального анализа и тестирования гипотез.

# 3. Основной принцип системы

Необходимо разделять четыре независимых понятия:

```text
Pattern Recognition ≠ Prediction ≠ Strategy ≠ Execution
```

Например, `Head & Shoulders` не должен автоматически означать `SELL`. Вместо этого:

```text
Head & Shoulders → Historical Examples → Future Outcomes → Statistics → Forecast
```

И только strategy layer может решать, использовать этот forecast для открытия позиции или нет.

# 4. Технологический стек

## 4.1 Backend

Python 3.12+: FastAPI, Pydantic, NumPy, Polars, SciPy, statsmodels, scikit-learn, numba, SQLAlchemy, Alembic.

## 4.2 Database

Основное хранилище — **PostgreSQL**, source of truth системы. Исторические и текущие рыночные данные хранятся в PostgreSQL.

Parquet допускается как формат импорта, экспорта, offline research datasets, backup/intermediate format. Приложение не должно зависеть от Parquet как от основного постоянного хранилища.

## 4.3 Frontend

Web UI является обязательным компонентом. *(См. решения: Angular + Taiga UI + Lightweight Charts.)*

```text
Browser → Angular / TypeScript → (REST / WebSocket) → FastAPI → Research / Market Engine → PostgreSQL
```

# 5. PostgreSQL

## 5.1 Основные требования

База данных должна хранить: инструменты; поставщиков данных; candles; таймфреймы; импортированные datasets; индикаторы и их версии; swing points; уровни; паттерны; historical matches; market events; outcomes; experiments; backtests; strategies; orders; trades; positions.

## 5.2 Raw и derived data

Необходимо разделить исходные и рассчитанные данные (`raw_candles`, `derived_candles`, `swing_points`, `levels`, `patterns`, `features`, `outcomes`).

Raw candles никогда не должны изменяться алгоритмами анализа. Derived data может пересчитываться при изменении версии соответствующего алгоритма.

# 6. Предлагаемая структура БД

```text
instruments, data_providers, timeframes
candles, data_imports, data_import_errors
indicator_definitions, indicator_values
swing_points, levels, level_sources, fibonacci_structures, pivot_structures
pattern_occurrences, pattern_points, pattern_features
market_context, market_events, event_features, event_outcomes
historical_matches
experiments, experiment_runs, experiment_results
strategies, backtests, backtest_trades
paper_orders, paper_trades
live_orders, live_trades, positions
```

Индексы по `instrument_id`, `timeframe`, `timestamp`. Основная выборка свечей оптимизирована под запрос `instrument + timeframe + timestamp range`. При росте объёма предусмотреть partitioning. TimescaleDB допускается, но не обязателен для MVP.

*(См. решения: root/contract/continuous; `indicator_values` не персистятся.)*

# 7. Импорт данных

Импорт обязателен. Пользователь должен иметь возможность импортировать исторические данные независимо от подключения к live data provider. Минимально: CSV, JSON, Parquet. Также импорт через provider API.

# 8. Import UI

Страница `Data → Import`. Пользователь выбирает Instrument, Provider, Timeframe, Date range, File / API source.

Конвейер: Parse → Validate → Normalize → Deduplicate → Detect missing data → Store in PostgreSQL.

UI показывает: Rows imported, Rows inserted, Duplicates, Errors, Missing intervals, Date range, Min/Max price.

# 9. Import validation

Перед записью проверять: timestamp; monotonic ordering; duplicate candles; `high >= max(open, close)`; `low <= min(open, close)`; `high >= low`; volume >= 0; корректность timeframe; отсутствие некорректных timestamp.

Каждая операция импорта имеет `data_import_id`. Ошибочные строки не отбрасываются молча; статистика ошибок сохраняется.

# 10. Data Provider abstraction

```python
class MarketDataProvider:
    def get_historical_candles(...): ...
    def subscribe_realtime(...): ...
```

Provider заменяем. Внутренняя система не зависит от формата конкретного API.

# 11. Candle model

```python
Candle:
    instrument_id
    timeframe
    timestamp
    open
    high
    low
    close
    volume
```

Дополнительно при наличии: `trade_count`, `quote_volume`, `provider_timestamp`. Все timestamps нормализуются в UTC.

# 12. Таймфреймы

Исходно: 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w; количество конфигурируется. *(Решение: 15m, 1h, 4h, 1d, 1w.)*

Система различает `chart timeframe` и `source timeframe`. Пример: chart BTCUSDT/15m; EMA 20/15m; EMA 200/1h; Pivot 1d; Fibonacci 4h; Pattern 15m.

# 13. Multi-timeframe engine

Центральный компонент. Любой indicator, level или structure имеет `source timeframe` и отображается на другом timeframe (например, 1h EMA200 → 15m chart, 4h Fibonacci → 15m chart).

Критическое требование: **никаких исторических данных из ещё не завершённого higher timeframe интервала.** Каждый derived value связан с `source_timestamp` и `available_timestamp`.

# 14. Indicator Engine

Индикаторы — независимые plugins (`name`, `version`, `parameters`, `calculate(...)`).

Первая версия:
- Trend: SMA, EMA, WMA, VWAP.
- Momentum: RSI, ROC, MACD.
- Volatility: ATR, Bollinger Bands.
- Volume: Volume MA, Relative Volume, OBV.

Новые индикаторы добавляются без изменения основного trading engine.

# 15. Indicator UI

На графике: `Add Indicator` → выбрать тип (EMA) → `period = 200`, `timeframe = 1h` → индикатор появляется на текущем графике. Параметры сохраняются в configuration/profile.

# 16. Market Structure Engine

Сущности: Swing High, Swing Low, Higher High, Lower High, Higher Low, Lower Low. Классификация: Uptrend, Downtrend, Range. Break of Structure / Change of Character — не обязательны для MVP.

# 17. Swing Point detection

Алгоритмы: fixed-window; percentage deviation; ATR deviation; ZigZag (например, 1.5% или 1.2 ATR).

SwingPoint: `timestamp`, `price`, `type`, `timeframe`, `detection_method`, `confirmed_at`.

# 18. Look-ahead bias

Одно из главных требований. Каждое событие разделяет `detected_at`, `confirmed_at`, `available_at`. Backtest использует только данные, существовавшие к `available_at`.

Нельзя использовать: будущие candles; будущие swing points; будущие Fibonacci endpoints; будущую higher-timeframe candle; информацию из будущих паттернов; будущие outcomes при расчёте текущего forecast.

# 19. Support / Resistance Engine

Источники: Swing High/Low; horizontal historical levels; Daily/Weekly/Monthly Pivot; Previous Day High/Low; Previous Week High/Low; Previous Close; Fibonacci; EMA/SMA/VWAP (участвуют в confluence, но сами не обязательно S/R).

# 20. Level object

```python
Level:
    price_low
    price_high
    timeframe
    strength
    touches
    created_at
    last_touch_at
    sources[]
```

Источники: swing_high, swing_low, pivot, fibonacci, previous_day_high, previous_week_high и т. д.

# 21. Level clustering

Близкие уровни объединяются в зоны (например, 68410 Swing High + 68418 Fib 61.8% + 68422 Daily R1 + 68425 PDH → Resistance Zone 68410–68425). Расстояние нормализуется по волатильности: `distance / ATR`.

# 22. Level strength

Strength — техническая характеристика, не торговый рейтинг. Учитывает: number of touches; timeframe; swing importance; age; independent sources; previous breakouts; distance between constituent levels; volume; rejection behaviour. Расчёт детерминированный и документированный.

# 23. Fibonacci Engine

Retracement: 23.6, 38.2, 50, 61.8, 78.6%. Extension: 100, 127.2, 161.8%. Structure хранит start point, end point, direction, timeframe, source swing algorithm.

# 24. Pivot Engine

Daily и Weekly Pivot: PP, R1–R3, S1–S3. Формулы и тип конфигурируемые.

# 25. Pattern Recognition Engine

```python
class PatternDetector:
    name
    version
    detect(context) -> list[PatternOccurrence]
```

PatternOccurrence: pattern_type, instrument, timeframe, start_timestamp, end_timestamp, detected_at, confirmed_at, available_at, points, features, quality.

# 26. Базовые паттерны

- Reversal: H&S, Inverse H&S, Double Top/Bottom, Triple Top/Bottom.
- Continuation: Triangle, Ascending/Descending Triangle, Wedge, Channel.
- Structural Events: Support/Resistance/Range breakout.

# 27. PIP Engine

Perceptually Important Points: raw price series → PIP extraction → compact geometric representation → pattern recognition. Режимы: fixed number of points; error threshold.

# 28. DTW Engine

Dynamic Time Warping для поиска похожих формаций. Нормализации: absolute, percentage, z-score, ATR (минимум percentage и ATR). Результат PatternMatch: historical_pattern_id, distance, normalized_distance, time_alignment.

# 29. Historical Pattern Database

Каждое подтверждённое событие сохраняется в PostgreSQL вместе со snapshot контекста, доступного на момент подтверждения.

# 30. Market Context

На момент события: price, EMA20/50/200, RSI, ATR, relative_volume, trend_state, volatility_state, distance_to_support/resistance, higher_timeframe_trend. Контекст версионирован.

# 31. Feature Engine

Feature vector события: distance_to_ema20/50/200, ema slopes, atr, atr_percentile, rsi, relative_volume, distance_to_support/resistance, distance_to_fib_38/50/61, distance_to_pivot, trend_state, volatility_state, pattern_width/height/quality. Разрешены multi-timeframe features (15m/1h/4h/1d).

# 32. Forward Outcome Engine

Для каждого события — последующее движение на горизонтах 1, 5, 10, 20, 50 свечей (конфигурируемо).

# 33. Outcome metrics

Forward Return `(close[t+n] / close[t]) - 1`; MFE; MAE; Time to Target (+0.5/+1/+2%); Time to Stop (−0.5/−1/−2%).

# 34. Prediction Engine

1. Empirical statistics: comparable events → outcomes → distribution.
2. Nearest Neighbours: по DTW, feature vector, market context.
3. ML (после накопления dataset): Logistic Regression, Random Forest, Gradient Boosting. ML не обязателен для MVP.

# 35. Forecast

Структурированный объект: instrument, timeframe, created_at, horizon, probabilities (up/down 0.5/1/2%), expected_return, median_return, MFE, MAE, sample_size, historical_matches, statistical_interval, contributing_features.

# 36. Не использовать один универсальный confidence score

Различать Pattern Quality, Historical Similarity, Statistical Reliability, Forecast Probability. Не объединять без ясной математической причины.

# 37. Market Regime Engine

Режимы: trend/range; bullish/bearish/neutral; low/medium/high volatility. Признаки: EMA slopes, ADX, ATR percentile, relative volume, distance from EMA200, higher timeframe trend.

# 38. Research Engine

Главный аналитический модуль. Отвечает на вопросы вида «как ведёт себя H&S на 15m», «как меняется статистика при bullish 1h trend», «что происходит у сильной resistance zone». Возвращает sample size, distribution, mean, median, percentiles, MAE, MFE, time-to-target, confidence interval.

# 39. Experiment system

Experiment: id, created_at, dataset_version, algorithm_versions, parameters, instrument, timeframe, train/validation/test period, result.

# 40. Backtesting

Historical candles → Market Context → Signals → Strategy → Orders → Execution model → Positions → Equity curve. Моделировать: commission, spread, slippage, order execution, stop, take profit, partial fills при необходимости.

# 41. Walk-forward testing

Rolling/walk-forward validation (train → validation → test, окно сдвигается). Результаты каждого окна сохраняются отдельно.

# 42. Data leakage tests

Автоматические тесты на использование будущих данных — отдельно для indicators, higher-timeframe values, ZigZag, swings, Fibonacci, levels, patterns, DTW, forecast features. Каждый алгоритм имеет timestamp availability contract.

# 43. UI как обязательная часть системы

Jupyter — только для ad-hoc анализа, прототипов и исследований. Основная работа — в Web UI: график рядом с результатами, быстрое переключение TF, индикаторы, MTF-контекст, переход к историческим аналогам, запуск backtest, replay, paper/live monitoring.

# 44. Основной Trading Chart UI

Заголовок (инструмент, TF, Replay/Live) → price chart с уровнями, EMA, pivot, fib → Volume → RSI → панель Pattern / Levels / Forecast.

# 45. Chart controls

Instrument, Timeframe, Date range; включение EMA, SMA, VWAP, RSI, MACD, ATR, Volume, Swing points, ZigZag, Support, Resistance, Fibonacci, Pivot, Patterns, Historical analogues, Higher timeframe context.

# 46. Research Panel

Current market context (trend, volatility, nearest support/resistance, 4h trend, 1h EMA200, 15m pattern candidate); после подтверждения — pattern, quality, historical cases, median return, P(+1%), MFE, MAE.

# 47. Historical Replay

Обязательный инструмент. Выбор инструмента, TF, даты → Replay: по одной свече или ускоренно. Показывается только информация, доступная в тот момент (new candle → recalculate context → detect events → update forecast). Будущие свечи скрыты.

# 48. Replay controls

Play, Pause, Next Candle, 10 Candles, 100 Candles, Speed, Restart; дополнительно Jump to date. Отображение «what system knew at the time».

# 49. Pattern debugging mode

Для выбранного паттерна: точки A, B, C, D…, результат каждого правила, pattern quality.

# 50. Historical Analogue UI

Current → DTW → Historical matches; для каждого: formation, confirmation point, future movement, return, MFE, MAE, similarity.

# 51. Future trajectory visualization

Исторические траектории после текущей точки + 25/50/75 перцентили.

# 52. Research Query UI

Страница Research: instrument, TF, pattern, period, conditions (например, 1h trend = bullish, ATR percentile > 50, price above EMA200) → sample size, return distribution, forward return, MFE, MAE, time to target, charts.

# 53. Backtest UI

Выбор instrument, TF, period, strategy, parameters, commission, slippage, spread → Net/Gross Return, Max Drawdown, Sharpe, Sortino, Win Rate, Profit Factor, Trades, Average Trade, MFE, MAE, Equity Curve. Эти метрики — не единственный критерий оценки.

# 54. Backtest result drill-down

Открыть конкретную сделку (entry, exit, reason, pattern, ближайший уровень, 1h trend, forecast) и перейти к участку графика.

# 55–59. Test Architecture

1. **Unit** — EMA, ATR, RSI, Pivot, Fibonacci, Swing, ZigZag, DTW, level clustering, pattern rules; test vectors для математики.
2. **Integration** — полный pipeline Candles → Indicators → Structure → Levels → Patterns → Features → Outcomes на фиксированном dataset в PostgreSQL.
3. **Historical Regression** — сохранённые ожидаемые результаты на реальных datasets; изменения видимы и объяснимы (Old/New/Diff).
4. **Replay** — система «в прошлом» не знает будущего; всё рассчитывается online. Главный тест против look-ahead bias.
5. **Out-of-sample** — Train/Validation/Test или walk-forward; test period не используется для выбора параметров.

# 60. Jupyter integration

`notebooks/` для исследований; notebook использует общие Python-модули системы, а не собственные копии алгоритмов. UI и notebook — одна backend implementation.

# 61. Trading Engine

Broker interface: `get_balance()`, `get_positions()`, `get_orders()`, `place_order()`, `cancel_order()`. Режимы BACKTEST, PAPER, LIVE; strategy не знает, в каком режиме выполняется.

# 62. Risk Engine

Отделён от Strategy: maximum position, maximum risk, stop distance, maximum daily loss, maximum exposure, maximum number of positions. Forecast не определяет размер позиции.

# 63. Alerts

Telegram, Web notification. События: New Pattern, Pattern Confirmed, Support/Resistance Entered, Breakout, Forecast Updated, Target/Stop Reached. Alert содержит причину и контекст.

# 64. Versioning

Версионировать Dataset, Indicator, Swing Algorithm, Level Engine, Pattern Engine, Feature Engine, Forecast Model, Strategy. Experiment/backtest содержит версии всех зависимостей.

# 65. Performance

MVP: 3–4 инструмента, 5–7 TF, несколько лет истории. Приоритет: Correctness > Reproducibility > Usability > Performance. Оптимизация — после profiling (дорогие: DTW, nearest-neighbor, сканирование паттернов, пересчёт длинных индикаторов).

# 66. Caching

Кэшировать indicator values, swing points, levels, patterns, features, historical matches. При новой свече пересчитывать только необходимый диапазон.

# 67. Database-driven research

Research Query → PostgreSQL → candles → algorithm → results → PostgreSQL → UI. Результаты experiments сохраняются в БД и открываются без повторного запуска.

# 68. Data snapshot

Experiment ссылается на конкретную версию dataset (например, BTCUSDT-15m-v4, диапазон, число строк), чтобы изменение истории у поставщика не ломало воспроизводимость.

# 69–78. MVP

- **MVP-1 — Data + UI**: PostgreSQL, CSV/Parquet import, 1–2 инструмента, 3 TF, свечи, график, переключение TF, Historical Replay. Цель — корректный график и доказательство корректности временной модели.
- **MVP-2 — Indicators + MTF**: EMA, SMA, RSI, ATR; MTF-индикаторы с корректной исторической доступностью.
- **MVP-3 — Structure + Levels**: Swing Points, ZigZag, S/R, Pivot, Fibonacci, Level clustering; отображение на графике.
- **MVP-4 — Patterns**: H&S, Inverse H&S, Double Top/Bottom, Triangle; unit, integration, visual examples, replay tests.
- **MVP-5 — Historical Statistics**: Pattern Database, Forward Outcomes, MAE, MFE, статистика по клику на паттерн.
- **MVP-6 — DTW / Historical Analogues**: PIP, Normalization, DTW, Similarity, Trajectories.
- **MVP-7 — Forecast**: Empirical, Nearest Neighbours, Distribution Visualization (ML — позже).
- **MVP-8 — Backtesting**: Event-driven, Commission, Slippage, Spread, Walk-forward, Out-of-sample.
- **MVP-9 — Paper Trading**: Paper Broker, Orders, Positions, PnL, Risk, Alerts.
- **MVP-10 — Live Trading** (только после paper): Real-time WebSocket, Broker Adapter, Live Orders, Position Management, Risk limits, Kill switch.

# 79. Что не делать в первой версии

Нейросети, reinforcement learning, сотни индикаторов/паттернов, массовый screening, distributed processing, Kafka, Kubernetes, микросервисы, сложная portfolio optimization.

Основной риск — не вычислительная мощность, а **false pattern + look-ahead bias + data leakage + overfitting**.

# 80. Критерий готовности Research Platform

Пользователь может: импортировать данные; проверить их качество; открыть инструмент; выбрать TF; отобразить индикаторы другого TF; увидеть swing points, S/R zones, Pivot, Fibonacci, паттерн и причину его обнаружения; открыть исторические аналоги и последующее поведение цены; запустить Replay и проверить алгоритм без знания будущего; создать Experiment; запустить backtest и walk-forward; сохранить experiment и повторно открыть его.

# 81. Ключевой сценарий пользователя

Открыть инструмент → выбрать 15m → добавить EMA200 1h → включить 4h levels, daily pivots, Fibonacci, S/R → увидеть паттерн → открыть детали → historical matches → future distributions → replay → проверить, как система видела момент в прошлом → создать experiment → walk-forward → только после этого включить паттерн в strategy.

# 82. Итоговая архитектура

```text
External Data (APIs / CSV) → Import Engine → PostgreSQL (Source of Truth)
    → Indicators | Structure (Swing/ZigZag) | Levels (S/R/Fib/Pivot)
    → Pattern Engine (H&S, Double Top, Triangle, PIP, DTW)
    → Feature Engine → Historical Events
    → Statistics | KNN/DTW | ML → Forecast Engine
    → Research UI (Chart, Replay, Research) | Trading Engine (Backtest, Paper, Live)
```

# 83. Главный принцип разработки

Implement → Unit Test → Visualize → Historical Replay → Generate Events → Calculate Outcomes → Train/Validation/Test → Walk-forward → Statistical validation → только потом Strategy.

Система — одновременно Charting Tool, Market Research Platform, Pattern Recognition Engine, Statistical Analysis Engine, Backtesting Platform и Paper Trading Platform, а не просто торговый бот.
