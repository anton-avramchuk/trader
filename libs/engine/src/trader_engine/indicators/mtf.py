"""Multi-timeframe проекция: значение старшего TF на баре младшего (ADR-0009).

Индикатор считается по барам source TF и проецируется на бары chart TF
**ступенькой**: значение source-бара появляется на первом баре графика, который
закрывается не раньше ``available_at`` (закрытия) source-бара. Формирующийся
старший бар не используется. Для одинакового TF это значение самого бара (оно
известно в его закрытие). Младший TF на старшем графике запрещён.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from trader_engine.indicators.base import BarInput, IndicatorSeries
from trader_engine.timeframes import TIMEFRAMES

_RANK = {code: rank for rank, code in enumerate(TIMEFRAMES)}


def validate_source_timeframe(chart_tf: str, source_tf: str) -> None:
    """``ValueError``, если TF неизвестен или source младше chart (запрещено)."""
    for code in (chart_tf, source_tf):
        if code not in _RANK:
            raise ValueError(
                f"Неизвестный таймфрейм {code!r}; доступны: {', '.join(TIMEFRAMES)}"
            )
    if _RANK[source_tf] < _RANK[chart_tf]:
        raise ValueError(
            f"Младший таймфрейм {source_tf} на старшем графике {chart_tf} запрещён: "
            "source TF должен быть не младше chart TF"
        )


@dataclass(frozen=True, slots=True)
class Projection:
    """Значение индикатора на баре графика."""

    values: dict[str, float | None]
    # ``valid`` — прогрев source-серии завершён; иначе значения ``None``.
    valid: bool
    # Какой source-бар дал значение (None — ни один ещё не закрылся).
    source_timestamp: datetime | None
    available_at: datetime | None


def project(
    chart_bars: Sequence[BarInput],
    source_bars: Sequence[BarInput],
    series: IndicatorSeries,
) -> list[Projection]:
    """Проекция ``series`` (посчитанной по ``source_bars``) на ``chart_bars``.

    Оба ряда по возрастанию времени. Значение source-бара ``j`` доступно барам
    графика с ``close_time >= source_bars[j].close_time``.
    """
    empty: dict[str, float | None] = {name: None for name in series.values}
    result: list[Projection] = []
    latest = -1
    for chart in chart_bars:
        while (
            latest + 1 < len(source_bars)
            and source_bars[latest + 1].close_time <= chart.close_time
        ):
            latest += 1
        if latest < 0:
            result.append(Projection(dict(empty), False, None, None))
            continue
        source = source_bars[latest]
        valid = latest >= series.valid_from
        values = series.at(latest) if valid else dict(empty)
        result.append(Projection(values, valid, source.timestamp, source.close_time))
    return result
