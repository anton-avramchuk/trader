"""Ядро симулятора (ADR-0027): одна позиция, вход на следующем баре, выходы, ролл.

Сигнал известен на закрытии бара ``signal.bar_index``; вход — по open следующего
бара в реальном фронтовом контракте. Бары приходят в ценах контракта и с множителем
``factor`` до шкалы continuous; стоп и цель сигнала заданы в шкале continuous и
переводятся в цены контракта делением на ``factor`` текущего бара (после ролла тот
же уровень получает цену нового контракта).

Правила внутри бара (начиная с бара входа): гэп через стоп — исполнение по open;
гэп через цель — по open; если в диапазоне бара достижимы и стоп, и цель — сначала
стоп (пессимистично) + ``ambiguous_bar``. Выход по времени — close бара
``entry_bar + max_bars − 1`` (как горизонт MVP-5). На ролле позиция закрывается по
close последнего бара старого контракта и открывается по open первого бара нового.
Издержки: полуспред и проскальзывание в тиках на каждую сторону (против позиции);
комиссия — за контракт на сторону, в валюте комиссии.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Side = Literal["long", "short"]
Reason = Literal["stop", "target", "time", "roll", "end_of_data"]


@dataclass(frozen=True, slots=True)
class SimBar:
    """Бар в ценах контракта; ``factor`` — множитель до шкалы continuous."""

    timestamp: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    contract_id: int
    factor: float = 1.0


@dataclass(frozen=True, slots=True)
class Signal:
    """Сигнал на закрытии бара ``bar_index``; стоп и цель — в шкале continuous."""

    bar_index: int
    side: Side
    stop: float | None = None
    target: float | None = None
    max_bars: int | None = None
    ref: str | None = None


@dataclass(frozen=True, slots=True)
class Costs:
    """Издержки: тики на сторону (против позиции) и комиссия за контракт на сторону."""

    half_spread_ticks: float = 0.0
    slippage_ticks: float = 0.0
    commission_per_contract: float = 0.0

    @property
    def ticks_per_side(self) -> float:
        return self.half_spread_ticks + self.slippage_ticks


@dataclass(frozen=True, slots=True)
class Leg:
    """Нога сделки в одном контракте: сделка, пересёкшая ролл, состоит из нескольких."""

    contract_id: int
    entry_bar: int
    entry_time: datetime
    entry_price: float
    exit_bar: int
    exit_time: datetime
    exit_price: float
    reason: Reason
    gross_ticks: float


@dataclass(frozen=True, slots=True)
class Trade:
    """Сделка: ноги, исход в тиках на контракт, MFE/MAE и издержки."""

    side: Side
    signal_index: int
    ref: str | None
    contracts: int
    legs: list[Leg]
    reason: Reason
    gross_ticks: float
    cost_ticks: float
    mfe_ticks: float
    mae_ticks: float
    commission: float
    ambiguous_bar: bool = False

    @property
    def net_ticks(self) -> float:
        return self.gross_ticks - self.cost_ticks

    @property
    def rolled(self) -> bool:
        return len(self.legs) > 1

    @property
    def entry_bar(self) -> int:
        return self.legs[0].entry_bar

    @property
    def exit_bar(self) -> int:
        return self.legs[-1].exit_bar


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


def _sign(side: Side) -> int:
    return 1 if side == "long" else -1


class _Run:
    """Состояние одной сделки."""

    def __init__(
        self,
        bars: Sequence[SimBar],
        signal: Signal,
        tick: float,
        costs: Costs,
        contracts: int,
    ) -> None:
        self.bars = bars
        self.signal = signal
        self.tick = tick
        self.costs = costs
        self.contracts = contracts
        self.sign = _sign(signal.side)
        self.legs: list[Leg] = []
        self.realized = 0.0  # тики закрытых ног
        self.mfe = 0.0
        self.mae = 0.0
        self.ambiguous = False
        self.leg_start = signal.bar_index + 1
        self.leg_price = bars[self.leg_start].open
        self.first_bar = self.leg_start

    def ticks(self, price: float) -> float:
        return self.sign * (price - self.leg_price) / self.tick

    def extend(self, low_price: float, high_price: float) -> None:
        """Экскурсии текущей ноги плюс уже реализованное."""
        a, b = self.ticks(low_price), self.ticks(high_price)
        self.mfe = max(self.mfe, self.realized + max(a, b))
        self.mae = min(self.mae, self.realized + min(a, b))

    def close_leg(self, index: int, price: float, reason: Reason) -> None:
        bar, start = self.bars[index], self.bars[self.leg_start]
        self.legs.append(
            Leg(
                bar.contract_id,
                self.leg_start,
                start.timestamp,
                self.leg_price,
                index,
                bar.close_time,
                price,
                reason,
                self.ticks(price),
            )
        )
        self.realized += self.ticks(price)

    def level(self, value: float | None, bar: SimBar) -> float | None:
        return None if value is None else value / bar.factor

    def exit_in_bar(self, index: int) -> tuple[float, Reason] | None:
        """Цена и причина выхода внутри бара ``index`` или ``None``."""
        bar, signal = self.bars[index], self.signal
        stop = self.level(signal.stop, bar)
        target = self.level(signal.target, bar)
        long = self.sign > 0
        stop_gap = stop is not None and (bar.open <= stop if long else bar.open >= stop)
        target_gap = target is not None and (
            bar.open >= target if long else bar.open <= target
        )
        stop_hit = stop is not None and (bar.low <= stop if long else bar.high >= stop)
        target_hit = target is not None and (
            bar.high >= target if long else bar.low <= target
        )
        if stop_gap:
            return bar.open, "stop"
        if target_gap:
            return bar.open, "target"
        if stop_hit and target_hit:
            self.ambiguous = True
            assert stop is not None
            return stop, "stop"
        if stop_hit:
            assert stop is not None
            return stop, "stop"
        if target_hit:
            assert target is not None
            return target, "target"
        return None

    def run(self) -> Trade:
        bars, signal = self.bars, self.signal
        for index in range(self.leg_start, len(bars)):
            bar = bars[index]
            if (
                index > self.leg_start
                and bar.contract_id != bars[index - 1].contract_id
            ):
                self.close_leg(index - 1, bars[index - 1].close, "roll")
                self.leg_start, self.leg_price = index, bar.open
            exit_ = self.exit_in_bar(index)
            if exit_ is not None:
                price, reason = exit_
                self.extend(min(bar.open, price), max(bar.open, price))
                self.close_leg(index, price, reason)
                return self.finish(reason)
            self.extend(bar.low, bar.high)
            held = index - self.first_bar + 1
            if signal.max_bars is not None and held >= signal.max_bars:
                self.close_leg(index, bar.close, "time")
                return self.finish("time")
        last = len(bars) - 1
        self.close_leg(last, bars[last].close, "end_of_data")
        return self.finish("end_of_data")

    def finish(self, reason: Reason) -> Trade:
        fills = 2 * len(self.legs)
        return Trade(
            side=self.signal.side,
            signal_index=self.signal.bar_index,
            ref=self.signal.ref,
            contracts=self.contracts,
            legs=self.legs,
            reason=reason,
            gross_ticks=self.realized,
            cost_ticks=fills * self.costs.ticks_per_side,
            mfe_ticks=self.mfe,
            mae_ticks=self.mae,
            commission=fills * self.contracts * self.costs.commission_per_contract,
            ambiguous_bar=self.ambiguous,
        )


def simulate(
    bars: Sequence[SimBar],
    signals: Sequence[Signal],
    *,
    tick_size: float,
    costs: Costs | None = None,
    contracts: int = 1,
) -> SimulationResult:
    """Прогон сигналов по барам: одна позиция, сигналы при открытой — пропускаются."""
    if tick_size <= 0:
        raise ValueError("tick_size должен быть положительным")
    if contracts < 1:
        raise ValueError("contracts должно быть не меньше 1")
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
        trade = _Run(bars, signal, tick_size, active, contracts).run()
        trades.append(trade)
        free_from = trade.exit_bar
    return SimulationResult(trades, Skipped(busy, no_next, invalid))
