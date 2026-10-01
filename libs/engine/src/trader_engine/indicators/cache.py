"""LRU-кэш значений индикаторов с инкрементальным дополнением хвоста (ADR-0010).

Ключ — ``(dataset_key, имя, версия, params_hash, source_tf)``, где ``dataset_key``
идентифицирует набор баров (версия датасета, серия). Индикаторы в БД не
хранятся. Если новый запрос — продолжение сохранённого (те же первые бары),
машина продолжает с сохранённого состояния и считает только хвост; если
исходные бары изменились (пересборка, правка истории) — считается заново.
"""

from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field

from trader_engine.indicators.base import (
    BarInput,
    Indicator,
    IndicatorSeries,
    run,
)
from trader_engine.indicators.registry import params_hash

CacheKey = tuple[str, str, int, str, str]
Signature = tuple[object, ...]


def _signature(bar: BarInput) -> Signature:
    return (
        bar.timestamp,
        bar.close_time,
        bar.open,
        bar.high,
        bar.low,
        bar.close,
        bar.volume,
    )


@dataclass(slots=True)
class _Entry:
    machine: Indicator
    series: IndicatorSeries
    # Отпечатки первого, среднего и последнего бара: изменение масштаба или
    # данных задним числом не даёт принять чужой хвост за продолжение.
    signatures: list[Signature]
    length: int


@dataclass(slots=True)
class CacheStats:
    hits: int = 0
    extends: int = 0
    misses: int = 0
    bars_computed: int = 0


@dataclass(slots=True)
class IndicatorCache:
    maxsize: int = 64
    stats: CacheStats = field(default_factory=CacheStats)
    _entries: OrderedDict[CacheKey, _Entry] = field(
        default_factory=lambda: OrderedDict()
    )

    def key(self, dataset_key: str, indicator: Indicator, source_tf: str) -> CacheKey:
        return (
            dataset_key,
            indicator.name,
            indicator.version,
            params_hash(indicator),
            source_tf,
        )

    @staticmethod
    def _checkpoints(bars: Sequence[BarInput], length: int) -> list[Signature]:
        indexes = sorted({0, length // 2, length - 1})
        return [_signature(bars[i]) for i in indexes]

    def compute(
        self,
        dataset_key: str,
        source_tf: str,
        indicator: Indicator,
        bars: Sequence[BarInput],
    ) -> IndicatorSeries:
        """Значения индикатора по ``bars`` (кэш и продолжение хвоста)."""
        if not bars:
            return IndicatorSeries(
                {name: [] for name in indicator.outputs}, indicator.warmup_bars
            )
        key = self.key(dataset_key, indicator, source_tf)
        entry = self._entries.get(key)
        if entry is not None and self._continues(entry, bars):
            self._entries.move_to_end(key)
            if len(bars) == entry.length:
                self.stats.hits += 1
                return self._copy(entry.series)
            self.stats.extends += 1
            added = len(bars) - entry.length
            machine = entry.machine.snapshot()
            tail = run(machine, bars[entry.length :])
            for name in machine.outputs:
                entry.series.values[name].extend(tail.values[name])
            entry.machine = machine
            entry.length = len(bars)
            entry.signatures = self._checkpoints(bars, len(bars))
            self.stats.bars_computed += added
            return self._copy(entry.series)

        self.stats.misses += 1
        machine = indicator.snapshot()
        series = run(machine, bars)
        self.stats.bars_computed += len(bars)
        self._entries[key] = _Entry(
            machine, series, self._checkpoints(bars, len(bars)), len(bars)
        )
        self._entries.move_to_end(key)
        while len(self._entries) > self.maxsize:
            self._entries.popitem(last=False)
        return self._copy(series)

    @staticmethod
    def _continues(entry: _Entry, bars: Sequence[BarInput]) -> bool:
        if len(bars) < entry.length:
            return False
        indexes = sorted({0, entry.length // 2, entry.length - 1})
        return [_signature(bars[i]) for i in indexes] == entry.signatures

    @staticmethod
    def _copy(series: IndicatorSeries) -> IndicatorSeries:
        return IndicatorSeries(
            {name: list(values) for name, values in series.values.items()},
            series.warmup_bars,
        )

    def __len__(self) -> int:
        return len(self._entries)
