"""Покрытие данных календарём: пропущенные интервалы и свечи вне сессий.

Разрывы по расписанию (ночь, выходные, клиринг, праздники) пропусками не
считаются: ожидаемые минуты берутся только из окон сессий календаря.
"""

from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from trader_engine.calendar import TradingCalendar

MINUTE = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class Coverage:
    """Итог анализа диапазона ``[первая свеча, последняя свеча]``."""

    # Склеенные интервалы ``[start, end)`` без свечей внутри сессий.
    missing_intervals: list[tuple[datetime, datetime]] = field(
        default_factory=list[tuple[datetime, datetime]]
    )
    missing_minutes: int = 0
    # Свечи, время которых не попадает ни в одно окно сессии календаря.
    outside_session: list[datetime] = field(default_factory=list[datetime])


def analyze_coverage(timestamps: list[datetime], calendar: TradingCalendar) -> Coverage:
    """Сравнить время свечей с окнами сессий.

    ``timestamps`` — уникальные, кратные минуте, по возрастанию (UTC, aware).
    Проверяется только диапазон от первой до последней свечи: что было до и
    после, этому источнику неизвестно.
    """
    if not timestamps:
        return Coverage()
    first = timestamps[0].astimezone(UTC)
    end = timestamps[-1].astimezone(UTC) + MINUTE

    missing: list[tuple[datetime, datetime]] = []
    missing_minutes = 0
    inside = 0
    covered = [False] * len(timestamps)

    for session in calendar.sessions_between(first, end):
        window_start = max(session.start, first)
        window_end = min(session.end, end)
        if window_start >= window_end:
            continue
        low = bisect_left(timestamps, window_start)
        high = bisect_left(timestamps, window_end)
        present = high - low
        inside += present
        for index in range(low, high):
            covered[index] = True
        missing_minutes += int((window_end - window_start) / MINUTE) - present

        cursor = window_start
        for index in range(low, high):
            moment = timestamps[index]
            if moment > cursor:
                missing.append((cursor, moment))
            cursor = moment + MINUTE
        if cursor < window_end:
            missing.append((cursor, window_end))

    merged: list[tuple[datetime, datetime]] = []
    for start, stop in missing:
        if merged and merged[-1][1] == start:
            merged[-1] = (merged[-1][0], stop)
        else:
            merged.append((start, stop))

    outside = [
        moment
        for moment, is_covered in zip(timestamps, covered, strict=True)
        if not is_covered
    ]
    assert inside == len(timestamps) - len(outside)
    return Coverage(
        missing_intervals=merged,
        missing_minutes=missing_minutes,
        outside_session=outside,
    )
