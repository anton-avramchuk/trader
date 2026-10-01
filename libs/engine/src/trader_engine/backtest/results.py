"""Деньги, equity и метрики бэктеста (ADR-0027).

Сделки симулятора выражены в тиках на контракт. Здесь они переводятся в пункты
цены (валюта котировки) и в ₽ по историческому ``step_price`` контракта на день
выхода каждой ноги (на ролле — свой у каждой ноги), затем считаются метрики. Все
метрики считаются по одной выбранной единице (``ticks``, ``points`` или ``rub``),
чтобы Sharpe и просадка не смешивали разные валюты. Комиссия — в ₽: в единицах
``ticks``/``points`` показывается отдельно, а в ``rub`` вычитается из результата.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from math import sqrt
from statistics import mean, stdev
from typing import Literal

from trader_engine.backtest.simulator import Trade

Unit = Literal["ticks", "points", "rub"]
# (стоимость шага в ₽, оценка ли это — на день нет собственной записи) или None.
StepPriceLookup = Callable[[int, datetime], tuple[float, bool] | None]

TRADING_DAYS = 252
MIN_TRADES = 30


@dataclass(frozen=True, slots=True)
class PricedTrade:
    """Сделка в деньгах: пункты цены на все контракты и ₽ (``None`` без step_price)."""

    trade: Trade
    exit_time: datetime
    gross_points: float
    net_points: float
    commission_rub: float
    gross_rub: float | None
    net_rub: float | None
    step_price_estimated: bool
    points_per_tick: float
    # ₽ за тик на все контракты по step_price последней ноги (для MFE/MAE в ₽)
    rub_per_tick: float | None


def price_trades(
    trades: Sequence[Trade], *, tick_size: float, step_price: StepPriceLookup
) -> list[PricedTrade]:
    """Деньги по сделкам: пункты — всегда, ₽ — если известен ``step_price`` всех ног."""
    priced: list[PricedTrade] = []
    for trade in trades:
        per_leg_cost = trade.cost_ticks / len(trade.legs)
        gross_rub = net_rub = 0.0
        known, estimated = True, False
        last_step = 0.0
        for leg in trade.legs:
            found = step_price(leg.contract_id, leg.exit_time)
            if found is None:
                known = False
                break
            value, is_estimate = found
            last_step = value
            estimated = estimated or is_estimate
            gross_rub += leg.gross_ticks * value * trade.contracts
            net_rub += (leg.gross_ticks - per_leg_cost) * value * trade.contracts
        priced.append(
            PricedTrade(
                trade=trade,
                exit_time=trade.legs[-1].exit_time,
                gross_points=trade.gross_ticks * tick_size * trade.contracts,
                net_points=trade.net_ticks * tick_size * trade.contracts,
                commission_rub=trade.commission,
                gross_rub=gross_rub if known else None,
                net_rub=net_rub - trade.commission if known else None,
                step_price_estimated=estimated,
                points_per_tick=tick_size * trade.contracts,
                rub_per_tick=last_step * trade.contracts if known else None,
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
    rolled: bool


def records(priced: Sequence[PricedTrade], unit: Unit) -> tuple[list[Record], int]:
    """Записи в единице ``unit`` и число сделок без ₽ (для ``rub``)."""
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
                item.gross_rub is None
                or item.net_rub is None
                or item.rub_per_tick is None
            ):
                missing += 1
                continue
            gross, net, scale = item.gross_rub, item.net_rub, item.rub_per_tick
        found.append(
            Record(
                item.exit_time.date(),
                gross,
                net,
                trade.mfe_ticks * scale,
                trade.mae_ticks * scale,
                trade.ambiguous_bar,
                trade.rolled,
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
    rolled_share: float | None
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
        rolled_share=sum(r.rolled for r in ordered) / count,
        warnings=warnings,
    )
