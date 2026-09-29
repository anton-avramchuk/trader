"""Расписание срочного рынка MOEX (FORTS) как правила с датами действия.

ВАЖНО: режим «с 23.03.2026» подтверждён по moex.com/s3886. Режим «до 23.03.2026»
восстановлен по памяти и **не проверен**: его нужно сверить с реальным покрытием
свечей ISS (задачи про импорт и тестовый датасет), а начало действия
(2020-01-01 — начало доступной истории ISS) — уточнить. Расписание хранится в БД
и редактируется в UI, эти правила — только начальные данные и эталон для тестов.
"""

from datetime import date, time

from trader_engine.calendar.calendar import TradingCalendar
from trader_engine.calendar.model import SessionRule, SessionWindow

TIMEZONE = "Europe/Moscow"
REGIME_CHANGE = date(2026, 3, 23)

# До 23.03.2026: вечерняя сессия относится к следующему торговому дню;
# клиринги 14:00–14:05 и 18:45–19:05, аукцион 09:50–10:00. Выходных сессий нет
# (не проверено).
LEGACY_RULE = SessionRule(
    effective_from=date(2020, 1, 1),
    effective_to=date(2026, 3, 22),
    weekday_windows=(
        SessionWindow("morning", time(7, 0), time(9, 50)),
        SessionWindow("main", time(10, 0), time(14, 0)),
        SessionWindow("main_2", time(14, 5), time(18, 45)),
        SessionWindow("evening", time(19, 5), time(23, 50), next_day=True),
    ),
    weekend_windows=(),
    bar_anchor=time(7, 0),
)

# С 23.03.2026: единая торговая сессия 07:00–23:50, вечерняя относится к текущему
# дню, выходные сессии 10:00–19:00.
UNIFIED_RULE = SessionRule(
    effective_from=REGIME_CHANGE,
    effective_to=None,
    weekday_windows=(
        SessionWindow("morning", time(7, 0), time(9, 0)),
        SessionWindow("main", time(9, 0), time(19, 0)),
        SessionWindow("evening", time(19, 0), time(23, 50)),
    ),
    weekend_windows=(SessionWindow("weekend", time(10, 0), time(19, 0)),),
    bar_anchor=time(7, 0),
)

MOEX_FORTS_RULES = (LEGACY_RULE, UNIFIED_RULE)


def moex_forts_calendar(holidays: tuple[date, ...] = ()) -> TradingCalendar:
    return TradingCalendar(TIMEZONE, MOEX_FORTS_RULES, holidays)
