"""Ядро симулятора (ADR-0027, ADR-0028): одна позиция, вход на следующем баре.

Сигнал известен на закрытии бара ``signal.bar_index``; вход — по open следующего
бара. Цены баров — те же, что на графике (ряд один, без склеек).

Правила внутри бара (начиная с бара входа): гэп через стоп — исполнение по open;
гэп через цель — по open; если в диапазоне бара достижимы и стоп, и цель — сначала
стоп (пессимистично) + ``ambiguous_bar``. Выход по времени — close бара
``entry_bar + max_bars − 1`` (как горизонт MVP-5); позиция, не закрытая до конца
данных, закрывается по close последнего бара (``end_of_data``).
Издержки: полуспред и проскальзывание в тиках на каждую сторону (против позиции);
комиссия — за единицу количества на сторону, в валюте комиссии.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Side = Literal["long", "short"]
Reason = Literal["stop", "target", "time", "end_of_data"]


@dataclass(frozen=True, slots=True)
class SimBar:
    timestamp: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class Signal:
    """Сигнал на закрытии бара ``bar_index``."""

    bar_index: int
    side: Side
    stop: float | None = None
    target: float | None = None
    max_bars: int | None = None
    ref: str | None = None
    # close сигнального бара — для связи сделки с уровнями
    ref_price: float | None = None


@dataclass(frozen=True, slots=True)
class Costs:
    """Издержки: тики на сторону (против позиции) и комиссия за единицу на сторону."""

    half_spread_ticks: float = 0.0
    slippage_ticks: float = 0.0
    commission_per_unit: float = 0.0

    @property
    def ticks_per_side(self) -> float:
        return self.half_spread_ticks + self.slippage_ticks


@dataclass(frozen=True, slots=True)
class Trade:
    """Сделка: вход, выход, исход в тиках на единицу, MFE/MAE и издержки."""

    side: Side
    signal_index: int
    ref: str | None
    quantity: int
    entry_bar: int
    entry_time: datetime
    entry_price: float
    exit_bar: int
    exit_time: datetime
    exit_price: float
    reason: Reason
    gross_ticks: float
    cost_ticks: float
    mfe_ticks: float
    mae_ticks: float
    commission: float
    ambiguous_bar: bool = False
    signal_price: float | None = None

    @property
    def net_ticks(self) -> float:
        return self.gross_ticks - self.cost_ticks


@dataclass(frozen=True, slots=True)
class Skipped:
    """Сигналы, не ставшие сделками: позиция уже открыта или нет следующего бара."""

    busy: int = 0
    no_next_bar: int = 0
    invalid: int = 0


@dataclass(frozen=True, slots=True)
class SimulationResult:
    trades: list[Trade]
    skipped: Skipped = field(default_factory=Skipped)


def _exit_in_bar(
    bar: SimBar, signal: Signal, sign: int
) -> tuple[float, Reason, bool] | None:
    """Цена, причина и признак неоднозначного бара или ``None``."""
    stop, target = signal.stop, signal.target
    long = sign > 0
    if stop is not None and (bar.open <= stop if long else bar.open >= stop):
        return bar.open, "stop", False
    if target is not None and (bar.open >= target if long else bar.open <= target):
        return bar.open, "target", False
    stop_hit = stop is not None and (bar.low <= stop if long else bar.high >= stop)
    target_hit = target is not None and (
        bar.high >= target if long else bar.low <= target
    )
    if stop_hit and target_hit:
        assert stop is not None
        return stop, "stop", True
    if stop_hit:
        assert stop is not None
        return stop, "stop", False
    if target_hit:
        assert target is not None
        return target, "target", False
    return None


def _run(
    bars: Sequence[SimBar],
    signal: Signal,
    tick: float,
    costs: Costs,
    quantity: int,
) -> Trade:
    sign = 1 if signal.side == "long" else -1
    entry = signal.bar_index + 1
    entry_price = bars[entry].open
    mfe = mae = 0.0
    ambiguous = False
    outcome: tuple[int, float, Reason] = (len(bars) - 1, bars[-1].close, "end_of_data")
    for index in range(entry, len(bars)):
        bar = bars[index]
        found = _exit_in_bar(bar, signal, sign)
        if found is not None:
            price, why, both = found
            ambiguous = both
            # на баре выхода экскурсии ограничены путём open → цена выхода
            low, high = min(bar.open, price), max(bar.open, price)
            outcome = (index, price, why)
        else:
            low, high = bar.low, bar.high
        moves = (sign * (low - entry_price) / tick, sign * (high - entry_price) / tick)
        mfe = max(mfe, *moves)
        mae = min(mae, *moves)
        if found is not None:
            break
        if signal.max_bars is not None and index - entry + 1 >= signal.max_bars:
            outcome = (index, bar.close, "time")
            break
    exit_bar, exit_price, reason = outcome
    return Trade(
        side=signal.side,
        signal_index=signal.bar_index,
        ref=signal.ref,
        quantity=quantity,
        entry_bar=entry,
        entry_time=bars[entry].timestamp,
        entry_price=entry_price,
        exit_bar=exit_bar,
        exit_time=bars[exit_bar].close_time,
        exit_price=exit_price,
        reason=reason,
        gross_ticks=sign * (exit_price - entry_price) / tick,
        cost_ticks=2 * costs.ticks_per_side,
        mfe_ticks=mfe,
        mae_ticks=mae,
        commission=2 * quantity * costs.commission_per_unit,
        ambiguous_bar=ambiguous,
        signal_price=signal.ref_price,
    )


def simulate(
    bars: Sequence[SimBar],
    signals: Sequence[Signal],
    *,
    tick_size: float,
    costs: Costs | None = None,
    quantity: int = 1,
) -> SimulationResult:
    """Прогон сигналов по барам: одна позиция, сигналы при открытой — пропускаются."""
    if tick_size <= 0:
        raise ValueError("tick_size должен быть положительным")
    if quantity < 1:
        raise ValueError("quantity должно быть не меньше 1")
    active = costs or Costs()
    trades: list[Trade] = []
    busy = no_next = invalid = 0
    free_from = 0  # сигналы на барах раньше — пока позиция открыта
    for signal in sorted(signals, key=lambda s: s.bar_index):
        if not 0 <= signal.bar_index < len(bars):
            invalid += 1
            continue
        if signal.bar_index < free_from:
            busy += 1
            continue
        if signal.bar_index + 1 >= len(bars):
            no_next += 1
            continue
        trade = _run(bars, signal, tick_size, active, quantity)
        trades.append(trade)
        free_from = trade.exit_bar
    return SimulationResult(trades, Skipped(busy, no_next, invalid))
