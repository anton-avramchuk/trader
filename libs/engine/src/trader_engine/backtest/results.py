"""Деньги, equity и метрики бэктеста (ADR-0027, ADR-0028).

Сделки симулятора выражены в тиках на единицу. Здесь они переводятся в пункты цены
и, если у инструмента задана ``tick_value`` (стоимость тика в валюте счёта на
единицу), в деньги; затем считаются метрики. Все метрики считаются по одной выбранной
единице (``ticks``, ``points`` или ``money``), чтобы Sharpe и просадка не смешивали
разные величины. Комиссия — в валюте счёта: в ``ticks``/``points`` показывается
отдельно, а в ``money`` вычитается из результата.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from math import sqrt
from statistics import mean, stdev
from typing import Literal

from trader_engine.backtest.simulator import Trade

Unit = Literal["ticks", "points", "money"]

TRADING_DAYS = 252
MIN_TRADES = 30


@dataclass(frozen=True, slots=True)
class PricedTrade:
    """Сделка в деньгах: пункты на всё количество и деньги (``None`` без tick_value)."""

    trade: Trade
    exit_time: datetime
    gross_points: float
    net_points: float
    commission: float
    gross_money: float | None
    net_money: float | None
    points_per_tick: float
    # деньги за тик на всё количество (для MFE/MAE в деньгах)
    money_per_tick: float | None


def price_trades(
    trades: Sequence[Trade], *, tick_size: float, tick_value: float | None
) -> list[PricedTrade]:
    """Деньги по сделкам: пункты — всегда, деньги — если задана ``tick_value``."""
    priced: list[PricedTrade] = []
    for trade in trades:
        per_tick = None if tick_value is None else tick_value * trade.quantity
        priced.append(
            PricedTrade(
                trade=trade,
                exit_time=trade.exit_time,
                gross_points=trade.gross_ticks * tick_size * trade.quantity,
                net_points=trade.net_ticks * tick_size * trade.quantity,
                commission=trade.commission,
                gross_money=None if per_tick is None else trade.gross_ticks * per_tick,
                net_money=None
                if per_tick is None
                else trade.net_ticks * per_tick - trade.commission,
                points_per_tick=tick_size * trade.quantity,
                money_per_tick=per_tick,
            )
        )
    return priced


@dataclass(frozen=True, slots=True)
class Record:
    """Сделка в выбранной единице — вход метрик."""

    day: date
    gross: float
    net: float
    mfe: float
    mae: float
    ambiguous: bool


def records(priced: Sequence[PricedTrade], unit: Unit) -> tuple[list[Record], int]:
    """Записи в единице ``unit`` и число сделок без денег (для ``money``)."""
    found: list[Record] = []
    missing = 0
    for item in priced:
        trade = item.trade
        if unit == "ticks":
            gross, net, scale = trade.gross_ticks, trade.net_ticks, 1.0
        elif unit == "points":
            gross, net, scale = item.gross_points, item.net_points, item.points_per_tick
        else:
            if (
                item.gross_money is None
                or item.net_money is None
                or item.money_per_tick is None
            ):
                missing += 1
                continue
            gross, net, scale = item.gross_money, item.net_money, item.money_per_tick
        found.append(
            Record(
                item.exit_time.date(),
                gross,
                net,
                trade.mfe_ticks * scale,
                trade.mae_ticks * scale,
                trade.ambiguous_bar,
            )
        )
    return found, missing


@dataclass(frozen=True, slots=True)
class EquityPoint:
    day: date
    equity: float


@dataclass(frozen=True, slots=True)
class Metrics:
    """Метрики по закрытым сделкам; ``None`` — посчитать нельзя (мало данных)."""

    unit: Unit
    trades: int
    wins: int
    win_rate: float | None
    gross: float
    net: float
    average_trade: float | None
    profit_factor: float | None
    max_drawdown: float
    sharpe: float | None
    sortino: float | None
    average_mfe: float | None
    average_mae: float | None
    ambiguous_share: float | None
    warnings: list[str] = field(default_factory=list[str])


def equity_curve(items: Sequence[Record]) -> list[EquityPoint]:
    """Накопленный net после каждой сделки в порядке выхода."""
    total = 0.0
    curve: list[EquityPoint] = []
    for item in sorted(items, key=lambda r: r.day):
        total += item.net
        curve.append(EquityPoint(item.day, total))
    return curve


def max_drawdown(values: Sequence[float]) -> float:
    """Наибольшее падение equity от пика (≥ 0); старт — нулевая equity."""
    peak = worst = 0.0
    level = 0.0
    for step in values:
        level += step
        peak = max(peak, level)
        worst = max(worst, peak - level)
    return worst


def daily_series(items: Sequence[Record], days: Sequence[date]) -> list[float]:
    """Net по торговым дням периода (дни без сделок — 0)."""
    by_day: dict[date, float] = {}
    for item in items:
        by_day[item.day] = by_day.get(item.day, 0.0) + item.net
    return [by_day.get(day, 0.0) for day in sorted(set(days) | set(by_day))]


def _sharpe(daily: Sequence[float]) -> float | None:
    if len(daily) < 2:
        return None
    spread = stdev(daily)
    return None if spread == 0 else mean(daily) / spread * sqrt(TRADING_DAYS)


def _sortino(daily: Sequence[float]) -> float | None:
    if len(daily) < 2:
        return None
    downside = sqrt(sum(min(0.0, x) ** 2 for x in daily) / len(daily))
    return None if downside == 0 else mean(daily) / downside * sqrt(TRADING_DAYS)


def compute_metrics(
    items: Sequence[Record], days: Sequence[date], unit: Unit
) -> Metrics:
    """Метрики; ``days`` — торговые дни периода (для дневных Sharpe и Sortino)."""
    ordered = sorted(items, key=lambda r: r.day)
    count = len(ordered)
    warnings: list[str] = []
    if count == 0:
        return Metrics(
            unit,
            0,
            0,
            None,
            0.0,
            0.0,
            None,
            None,
            0.0,
            None,
            None,
            None,
            None,
            None,
            ["no_trades"],
        )
    if count < MIN_TRADES:
        warnings.append("small_sample")
    nets = [r.net for r in ordered]
    wins = sum(n > 0 for n in nets)
    profit = sum(n for n in nets if n > 0)
    loss = -sum(n for n in nets if n < 0)
    daily = daily_series(ordered, days)
    return Metrics(
        unit=unit,
        trades=count,
        wins=wins,
        win_rate=wins / count,
        gross=sum(r.gross for r in ordered),
        net=sum(nets),
        average_trade=mean(nets),
        profit_factor=None if loss == 0 else profit / loss,
        max_drawdown=max_drawdown(nets),
        sharpe=_sharpe(daily),
        sortino=_sortino(daily),
        average_mfe=mean(r.mfe for r in ordered),
        average_mae=mean(r.mae for r in ordered),
        ambiguous_share=sum(r.ambiguous for r in ordered) / count,
        warnings=warnings,
    )
