"""Расписание срочного рынка MOEX (FORTS) как правила с датами действия.

Откалибровано по данным MOEX ISS (задача #59): даты переходов найдены бисекцией
по минутным свечам самого ликвидного контракта BR, праздники — по дневной истории.
Скрипт и методика — ``scripts/calibrate_forts_calendar.py``, выводы — в ADR-0014.

Время окон — московское. Окна включают минуту аукциона открытия (08:59, 09:59,
06:59): сделки аукциона попадают в свечу этой минуты. Расписание хранится в БД и
редактируется в UI; эти правила — начальные данные и эталон для тестов.
"""

from datetime import date, time

from trader_engine.calendar.calendar import TradingCalendar
from trader_engine.calendar.model import SessionRule, SessionWindow
from trader_engine.calendar.moex_data import (
    MOEX_FORTS_HOLIDAYS,
    MOEX_FORTS_SPECIAL_DAYS,
)

TIMEZONE = "Europe/Moscow"
# Единая непрерывная сессия без клиринга (подтверждено moex.com/s3886 и данными).
REGIME_CHANGE = date(2026, 3, 23)


def _window(
    name: str, start: tuple[int, int], end: tuple[int, int], *, next_day: bool = False
) -> SessionWindow:
    return SessionWindow(name, time(*start), time(*end), next_day=next_day)


def _rule(
    start: date,
    end: date | None,
    weekday: tuple[SessionWindow, ...],
    weekend: tuple[SessionWindow, ...] = (),
) -> SessionRule:
    return SessionRule(
        effective_from=start,
        effective_to=end,
        weekday_windows=weekday,
        weekend_windows=weekend,
        # Сетка баров от 07:00: часовые и 15-минутные бары совпадают с часами.
        bar_anchor=time(7, 0),
    )


# --- окна ---------------------------------------------------------------------
_MORNING_0700 = _window("morning", (7, 0), (10, 0))
_MORNING_0859 = _window("morning", (8, 59), (10, 0))
_MAIN = _window("main", (10, 0), (14, 0))
_MAIN_AUCTION = _window("main", (9, 59), (14, 0))  # без утра: аукцион в 09:59
_MAIN_2_1845 = _window("main_2", (14, 5), (18, 45))
_MAIN_2_1850 = _window("main_2", (14, 5), (18, 50))
# Вечерняя сессия относится к следующему торговому дню (проверено по объёмам).
_EVENING_1900 = _window("evening", (19, 0), (23, 50), next_day=True)
_EVENING_1905 = _window("evening", (19, 5), (23, 50), next_day=True)
# Выходные сессии — часть торгового дня понедельника (проверено по объёмам).
_WEEKEND = (_window("weekend", (9, 59), (19, 0), next_day=True),)

# Единая сессия: клиринга нет, вечерняя относится к текущему дню.
_UNIFIED_MAIN = _window("main", (10, 0), (19, 0))
_UNIFIED_EVENING = _window("evening", (19, 0), (23, 50))

MOEX_FORTS_RULES = (
    # Без утренней сессии, вечерняя с 19:00.
    _rule(date(2020, 1, 1), date(2021, 2, 28), (_MAIN, _MAIN_2_1845, _EVENING_1900)),
    # Утренняя сессия 07:00–10:00.
    _rule(
        date(2021, 3, 1),
        date(2022, 2, 24),
        (_MORNING_0700, _MAIN, _MAIN_2_1845, _EVENING_1900),
    ),
    # Ограничения февраля–марта 2022: утро остановлено, вечер ещё торгуется.
    _rule(date(2022, 2, 25), date(2022, 3, 1), (_MAIN, _MAIN_2_1845, _EVENING_1900)),
    # Только основная сессия.
    _rule(date(2022, 3, 2), date(2022, 5, 29), (_MAIN, _MAIN_2_1845)),
    _rule(date(2022, 5, 30), date(2022, 7, 11), (_MAIN, _MAIN_2_1850)),
    # Вечерняя сессия вернулась с 19:05.
    _rule(date(2022, 7, 12), date(2022, 9, 11), (_MAIN, _MAIN_2_1850, _EVENING_1905)),
    # Утренняя сессия 09:00–10:00.
    _rule(
        date(2022, 9, 12),
        date(2024, 6, 12),
        (_MORNING_0859, _MAIN, _MAIN_2_1850, _EVENING_1905),
    ),
    # Утренняя сессия отменена (остаётся сделка аукциона в 09:59).
    _rule(
        date(2024, 6, 13),
        date(2025, 1, 26),
        (_MAIN_AUCTION, _MAIN_2_1850, _EVENING_1905),
    ),
    _rule(
        date(2025, 1, 27),
        date(2025, 8, 15),
        (_MORNING_0859, _MAIN, _MAIN_2_1850, _EVENING_1905),
    ),
    # Регулярные выходные сессии 10:00–19:00 (не каждые выходные — см. праздники).
    _rule(
        date(2025, 8, 16),
        date(2026, 3, 22),
        (_MORNING_0859, _MAIN, _MAIN_2_1850, _EVENING_1905),
        _WEEKEND,
    ),
    _rule(
        REGIME_CHANGE,
        date(2026, 7, 13),
        (_MORNING_0859, _UNIFIED_MAIN, _UNIFIED_EVENING),
        _WEEKEND,
    ),
    # Старт торгов в 07:00 (аукцион 06:50–07:00).
    _rule(
        date(2026, 7, 14),
        None,
        (_window("morning", (6, 59), (10, 0)), _UNIFIED_MAIN, _UNIFIED_EVENING),
        _WEEKEND,
    ),
)

__all__ = [
    "MOEX_FORTS_HOLIDAYS",
    "MOEX_FORTS_RULES",
    "MOEX_FORTS_SPECIAL_DAYS",
    "REGIME_CHANGE",
    "TIMEZONE",
    "moex_forts_calendar",
]


def moex_forts_calendar(
    extra_holidays: tuple[date, ...] = (),
) -> TradingCalendar:
    """Календарь FORTS; ``extra_holidays`` — дополнительные праздники (в тестах)."""
    return TradingCalendar(
        TIMEZONE,
        MOEX_FORTS_RULES,
        (*MOEX_FORTS_HOLIDAYS, *extra_holidays),
        MOEX_FORTS_SPECIAL_DAYS,
    )
