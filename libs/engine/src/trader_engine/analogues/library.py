"""Библиотека кандидатов (ADR-0025): формы исторических вхождений и запроса.

Формация любого вхождения (и запроса) — окно из ``window`` баров, заканчивающееся
баром входа; у вхождения это бар ``available_at`` события. Область поиска (scope:
свой инструмент или все того же TF) задаёт вызывающий — составом переданных рядов.
Здесь причинность: берётся только то, что было известно на ``as_of``, а окно
запроса и пересекающиеся с ним вхождения того же ряда исключаются.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from trader_engine.analogues.pip import (
    DEFAULT_PIP_POINTS,
    Normalization,
    Shape,
    window_shape,
)
from trader_engine.stats.occurrences import Occurrence
from trader_engine.stats.pipeline import Series, SeriesOccurrence

DEFAULT_WINDOW = 60
MIN_WINDOW = 10


@dataclass(frozen=True, slots=True)
class Formation:
    """Окно баров ``[start, end]`` (индексы ряда) и его нормализованная форма."""

    series_key: str
    start: int
    end: int
    shape: Shape


@dataclass(frozen=True, slots=True)
class Candidate:
    """Историческое вхождение с формацией перед входом."""

    formation: Formation
    occurrence: Occurrence
    series: Series

    @property
    def entry_index(self) -> int:
        return self.formation.end


def formation_at(
    series: Series,
    end: int,
    *,
    window: int = DEFAULT_WINDOW,
    k: int = DEFAULT_PIP_POINTS,
    mode: Normalization = "atr",
) -> Formation | None:
    """Формация, заканчивающаяся баром ``end``; ``None`` — мало баров или нет ATR."""
    if window < MIN_WINDOW:
        raise ValueError(f"window не меньше {MIN_WINDOW}")
    start = end - window + 1
    if start < 0 or end >= len(series.bars):
        return None
    shape = window_shape(series.bars[start : end + 1], series.atrs[end], k, mode)
    if shape is None:
        return None
    return Formation(series.key, start, end, shape)


def build_candidates(
    items: Iterable[SeriesOccurrence],
    *,
    as_of: datetime,
    window: int = DEFAULT_WINDOW,
    k: int = DEFAULT_PIP_POINTS,
    mode: Normalization = "atr",
    exclude: Formation | None = None,
) -> list[Candidate]:
    """Кандидаты из вхождений: известные на ``as_of``, с полной формацией.

    ``exclude`` — формация запроса: вхождения того же ряда, чьё окно с ней
    пересекается (включая само запрошенное вхождение), не берутся.
    """
    found: list[Candidate] = []
    for item in items:
        occurrence = item.occurrence
        entry = item.entry_index
        if entry is None or occurrence.available_at > as_of:
            continue
        formation = formation_at(item.series, entry, window=window, k=k, mode=mode)
        if formation is None:
            continue
        if (
            exclude is not None
            and formation.series_key == exclude.series_key
            and formation.start <= exclude.end
            and exclude.start <= formation.end
        ):
            continue
        found.append(Candidate(formation, occurrence, item.series))
    return sorted(
        found,
        key=lambda c: (
            c.occurrence.available_at,
            c.formation.series_key,
            c.occurrence.key,
        ),
    )
