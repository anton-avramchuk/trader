"""Арифметика окон дат: слияние, вычитание покрытого, нарезка на куски.

Окно — пара дат ``(первая, последняя)``, обе включены. Нужно для возобновляемой
загрузки: из запрошенного окна вычитаются уже загруженные, остаток режется на
куски, каждый из которых импортируется атомарно.
"""

from collections.abc import Iterable
from datetime import date, timedelta

DateWindow = tuple[date, date]

_DAY = timedelta(days=1)


def _valid(window: DateWindow) -> DateWindow:
    if window[0] > window[1]:
        raise ValueError(f"Окно {window[0]}..{window[1]}: начало позже конца")
    return window


def merge_windows(windows: Iterable[DateWindow]) -> list[DateWindow]:
    """Объединить пересекающиеся и соседние (встык по дням) окна."""
    merged: list[DateWindow] = []
    for start, end in sorted(_valid(window) for window in windows):
        if merged and start <= merged[-1][1] + _DAY:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def subtract_windows(
    window: DateWindow, covered: Iterable[DateWindow]
) -> list[DateWindow]:
    """Части ``window``, не покрытые ни одним окном из ``covered``."""
    start, end = _valid(window)
    gaps: list[DateWindow] = []
    cursor = start
    for covered_start, covered_end in merge_windows(covered):
        if covered_end < cursor:
            continue
        if covered_start > end:
            break
        if covered_start > cursor:
            gaps.append((cursor, covered_start - _DAY))
        cursor = max(cursor, covered_end + _DAY)
        if cursor > end:
            return gaps
    if cursor <= end:
        gaps.append((cursor, end))
    return gaps


def chunk_window(window: DateWindow, days: int) -> list[DateWindow]:
    """Нарезать окно на последовательные куски не длиннее ``days`` дней."""
    if days < 1:
        raise ValueError("Длина куска — не меньше одного дня")
    start, end = _valid(window)
    chunks: list[DateWindow] = []
    while start <= end:
        stop = min(start + timedelta(days=days - 1), end)
        chunks.append((start, stop))
        start = stop + _DAY
    return chunks
