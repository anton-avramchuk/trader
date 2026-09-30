"""Online replay: проверка индикатора против look-ahead (ADR-0003, spec §58).

Batch-результат (один прогон по всей истории) сравнивается с результатом, который
система получила бы «в моменте»: на выбранных барах ``t`` индикатор создаётся
заново и прогоняется **только** по ``bars[:t+1]``. Значения обязаны совпасть
точно: любое расхождение — утечка будущего в расчёт (или скрытое состояние, не
зависящее от баров).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from trader_engine.indicators.base import BarInput, Indicator, IndicatorSeries, run
from trader_engine.indicators.registry import available, create

MAX_REPORTED_MISMATCHES = 50


@dataclass(frozen=True, slots=True)
class Mismatch:
    """Расхождение batch и online на баре ``index`` по выходу ``output``."""

    index: int
    timestamp: datetime
    output: str
    batch: float | None
    online: float | None


@dataclass(slots=True)
class VerifyReport:
    indicator: str
    bars: int
    # Сколько позиций сравнено и сколько выходных значений на них.
    positions_checked: int
    values_checked: int
    mismatch_count: int = 0
    mismatches: list[Mismatch] = field(default_factory=lambda: [])

    @property
    def ok(self) -> bool:
        return self.mismatch_count == 0


def sample_positions(total: int, max_positions: int | None) -> list[int]:
    """Позиции для проверки: все, либо равномерная выборка (включая последнюю)."""
    if total <= 0:
        return []
    if max_positions is None or total <= max_positions:
        return list(range(total))
    step = (total - 1) / (max_positions - 1) if max_positions > 1 else 0
    positions = {round(i * step) for i in range(max_positions)}
    positions.add(total - 1)
    return sorted(positions)


def verify_online(
    factory: Callable[[], Indicator],
    bars: Sequence[BarInput],
    *,
    max_positions: int | None = None,
    batch: IndicatorSeries | None = None,
) -> VerifyReport:
    """Сверить batch с прогонами «с нуля» по префиксам на выбранных позициях.

    ``factory`` создаёт свежий экземпляр индикатора; ``batch`` можно передать
    готовый (иначе считается здесь). Значения сравниваются точно.
    """
    probe = factory()
    reference = batch if batch is not None else run(factory(), bars)
    positions = sample_positions(len(bars), max_positions)
    report = VerifyReport(
        indicator=probe.name,
        bars=len(bars),
        positions_checked=len(positions),
        values_checked=len(positions) * len(probe.outputs),
    )
    for t in positions:
        online = run(factory(), bars[: t + 1]).at(t)
        expected = reference.at(t)
        for output in probe.outputs:
            if online[output] != expected[output]:
                report.mismatch_count += 1
                if len(report.mismatches) < MAX_REPORTED_MISMATCHES:
                    report.mismatches.append(
                        Mismatch(
                            index=t,
                            timestamp=bars[t].timestamp,
                            output=output,
                            batch=expected[output],
                            online=online[output],
                        )
                    )
    return report


def verify_registry(
    bars: Sequence[BarInput], *, max_positions: int | None = None
) -> dict[str, VerifyReport]:
    """Проверить все зарегистрированные индикаторы с параметрами по умолчанию."""
    return {
        plugin.name: verify_online(
            lambda plugin=plugin: create(plugin.name),
            bars,
            max_positions=max_positions,
        )
        for plugin in available()
    }
