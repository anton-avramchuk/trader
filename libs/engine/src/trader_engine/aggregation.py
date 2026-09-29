"""Агрегация минутных свечей в бары 15m / 1h / 4h / 1d / 1w по торговому календарю.

Чистая, потоковая логика без БД (ADR-0004, ADR-0014, ADR-0018):

- границы баров, ``close_time`` и признак неполного бара берутся только из
  календаря; intraday — ячейки сетки (``TradingCalendar.bar_slot``), ``1d`` —
  торговый день, ``1w`` — торговая неделя;
- свечи вне сессий календаря в бары не попадают и **считаются**;
- **формирующийся бар не публикуется**: последний бар входа выдаётся, только
  если известно, что он закрыт (``complete_until``). ISS не отдаёт минуты без
  сделок, поэтому по отсутствию следующей свечи закрытость не определить.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from trader_engine.calendar import BarSlot, TradingCalendar
from trader_engine.ingest import Candle1m

TIMEFRAMES: dict[str, timedelta | None] = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": None,
    "1w": None,
}
INTRADAY = tuple(code for code, length in TIMEFRAMES.items() if length is not None)
SKIPPED_SAMPLE_LIMIT = 1000


@dataclass(frozen=True, slots=True)
class Bar:
    """Бар таймфрейма; ``timestamp`` — open time, ``close_time`` — конец (UTC)."""

    timeframe: str
    timestamp: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_count: int | None
    # Сколько минутных свечей вошло в бар.
    candles: int
    # Бар короче номинала: обрезан сессией, клирингом или границей сетки.
    is_partial: bool
    trading_day: date


@dataclass(slots=True)
class AggregationStats:
    candles_in: int = 0
    bars_out: int = 0
    skipped_outside_session: int = 0
    # Первые пропущенные моменты — для диагностики (не более SKIPPED_SAMPLE_LIMIT).
    skipped_sample: list[datetime] = field(default_factory=list[datetime])
    # Последний бар входа не выдан: неизвестно, закрыт ли он.
    last_bar_withheld: bool = False


@dataclass(slots=True)
class _Accumulator:
    key: object
    timestamp: datetime
    close_time: datetime
    trading_day: date
    is_partial: bool
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_count: int | None
    candles: int = 1

    def add(self, candle: Candle1m) -> None:
        self.high = max(self.high, candle.high)
        self.low = min(self.low, candle.low)
        self.close = candle.close
        self.volume += candle.volume
        self.trade_count = (
            None
            if self.trade_count is None or candle.trade_count is None
            else self.trade_count + candle.trade_count
        )
        self.candles += 1


class BarAggregator:
    """Потоковая агрегация свечей по возрастанию времени в бары одного таймфрейма."""

    def __init__(
        self,
        calendar: TradingCalendar,
        timeframe: str,
        *,
        complete_until: datetime | None = None,
    ) -> None:
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"Неизвестный таймфрейм {timeframe!r}")
        self._calendar = calendar
        self._timeframe = timeframe
        self._length = TIMEFRAMES[timeframe]
        self._complete_until = complete_until
        self._current: _Accumulator | None = None
        self._slot: BarSlot | None = None
        self._last_moment: datetime | None = None
        self._bounds: dict[object, tuple[datetime, datetime]] = {}
        self.stats = AggregationStats()

    # --- границы ------------------------------------------------------------

    def _classify(
        self, moment: datetime
    ) -> tuple[object, datetime, datetime, date, bool] | None:
        """Ключ бара, его начало, конец, торговый день и признак неполноты."""
        if self._length is not None:
            # Соседние свечи чаще всего в той же ячейке сетки: слот не пересчитываем.
            cached = self._slot
            if cached is not None and cached.contains(moment):
                slot = cached
            else:
                slot = self._calendar.bar_slot(moment, self._length)
                if slot is None:
                    return None
                self._slot = slot
            return (
                slot.start,
                slot.start,
                slot.close_time,
                slot.trading_day,
                slot.is_partial,
            )
        session = self._calendar.session_at(moment)
        if session is None:
            return None
        if self._timeframe == "1d":
            key: object = session.trading_day
            bounds = self._bounds.get(key)
            if bounds is None:
                found = self._calendar.trading_day_bounds(session.trading_day)
                assert found is not None
                bounds = self._bounds[key] = found
        else:
            week = self._calendar.trading_week_start(session.trading_day)
            key = week
            bounds = self._bounds.get(key)
            if bounds is None:
                found = self._calendar.trading_week_bounds(week)
                assert found is not None
                bounds = self._bounds[key] = found
        return key, bounds[0], bounds[1], session.trading_day, False

    # --- поток --------------------------------------------------------------

    def push(self, candle: Candle1m) -> list[Bar]:
        """Добавить свечу; вернуть бары, которые она закрыла (обычно 0 или 1)."""
        if self._last_moment is not None and candle.timestamp <= self._last_moment:
            raise ValueError(
                f"Свечи должны идти по возрастанию времени без повторов: "
                f"{candle.timestamp} после {self._last_moment}"
            )
        self._last_moment = candle.timestamp
        self.stats.candles_in += 1
        placement = self._classify(candle.timestamp)
        if placement is None:
            self.stats.skipped_outside_session += 1
            if len(self.stats.skipped_sample) < SKIPPED_SAMPLE_LIMIT:
                self.stats.skipped_sample.append(candle.timestamp)
            return []
        key, start, close_time, trading_day, partial = placement
        emitted: list[Bar] = []
        current = self._current
        if current is not None and current.key != key:
            emitted.append(self._to_bar(current))
            current = None
        if current is None:
            self._current = _Accumulator(
                key=key,
                timestamp=start,
                close_time=close_time,
                trading_day=trading_day,
                is_partial=partial,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                trade_count=candle.trade_count,
            )
        else:
            current.add(candle)
        self.stats.bars_out += len(emitted)
        return emitted

    def finish(self) -> list[Bar]:
        """Завершить поток: последний бар выдаётся, только если он закрыт."""
        current, self._current = self._current, None
        if current is None:
            return []
        if self._complete_until is None or current.close_time > self._complete_until:
            self.stats.last_bar_withheld = True
            return []
        self.stats.bars_out += 1
        return [self._to_bar(current)]

    def _to_bar(self, acc: _Accumulator) -> Bar:
        return Bar(
            timeframe=self._timeframe,
            timestamp=acc.timestamp,
            close_time=acc.close_time,
            open=acc.open,
            high=acc.high,
            low=acc.low,
            close=acc.close,
            volume=acc.volume,
            trade_count=acc.trade_count,
            candles=acc.candles,
            is_partial=acc.is_partial,
            trading_day=acc.trading_day,
        )


def iter_bars(
    candles: Iterable[Candle1m],
    calendar: TradingCalendar,
    timeframe: str,
    *,
    complete_until: datetime | None = None,
) -> Iterator[Bar]:
    """Бары потоком (статистика теряется; нужна — берите ``BarAggregator``)."""
    aggregator = BarAggregator(calendar, timeframe, complete_until=complete_until)
    for candle in candles:
        yield from aggregator.push(candle)
    yield from aggregator.finish()


def aggregate(
    candles: Iterable[Candle1m],
    calendar: TradingCalendar,
    timeframe: str,
    *,
    complete_until: datetime | None = None,
) -> tuple[list[Bar], AggregationStats]:
    """Бары и статистика за один вызов (для небольших входов и тестов)."""
    aggregator = BarAggregator(calendar, timeframe, complete_until=complete_until)
    bars: list[Bar] = []
    for candle in candles:
        bars.extend(aggregator.push(candle))
    bars.extend(aggregator.finish())
    return bars, aggregator.stats
