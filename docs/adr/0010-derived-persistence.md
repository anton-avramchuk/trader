# 0010. Что персистится из derived-данных

**Статус:** принято

## Решение
- Индикаторы не хранятся в БД: считаются на лету, in-process LRU-кэш по `(dataset_version, indicator, version, params, source_tf)`. У каждого индикатора — `warmup_bars`; до конца прогрева значения не показываются и не используются.
- Персистятся события и дорогие результаты: swing points, levels, fibonacci/pivot-структуры, паттерны, feature-снапшоты, outcomes, DTW-матчи — с `algorithm_version`, `params_hash`, `dataset_version_id`. Новая версия — новый прогон; старые результаты не трогаются.
- Профили графика хранятся в PostgreSQL.

## Последствия
Таблица `indicator_values` из §6 не создаётся.
