"""Continuous-серия фьючерса: расписание роллов, ratio-склейка, масштаб цен (ADR-0019).

Чистая логика без БД. Ролл происходит в начале торговой недели, поэтому бар
любого таймфрейма (включая ``1w``) целиком принадлежит одному контракту. Склейка
ratio: цены прошлых сегментов умножаются на накопленный коэффициент так, что
относительные изменения внутри сегмента совпадают с исходным контрактом, а
последний сегмент остаётся в реальных ценах.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal

from trader_engine.aggregation import Bar
from trader_engine.calendar import TradingCalendar

# Сколько торговых дней назад искать общий день для расчёта ratio.
RATIO_LOOKBACK_DAYS = 10


@dataclass(frozen=True, slots=True)
class ContractSpec:
    """Контракт для расписания роллов: ``expiration`` — последний день торгов."""

    contract_id: int
    expiration: date


@dataclass(frozen=True, slots=True)
class Roll:
    """Запланированное переключение с одного контракта на следующий."""

    from_contract_id: int
    to_contract_id: int
    week_start: date
    # Первый момент торговой недели ролла (UTC): с него бары берутся из нового.
    at: datetime


@dataclass(frozen=True, slots=True)
class RatioBasis:
    """Данные, по которым посчитан ratio: общий торговый день и закрытия."""

    trading_day: date
    from_close: Decimal
    to_close: Decimal

    @property
    def ratio(self) -> Decimal:
        return self.to_close / self.from_close


@dataclass(frozen=True, slots=True)
class Segment:
    """Отрезок времени ``[start, end)``, где фронтовым был ``contract_id``."""

    contract_id: int
    start: datetime | None
    end: datetime | None
    # Множитель цен сегмента до масштаба текущего (последнего) контракта.
    factor: Decimal


@dataclass(frozen=True, slots=True)
class ContinuousBar:
    """Бар continuous-серии: цены приведены к масштабу текущего контракта."""

    bar: Bar
    contract_id: int
    factor: Decimal


def trading_days_between(
    calendar: TradingCalendar, start: date, end: date
) -> list[date]:
    """Торговые дни в ``[start, end)``."""
    days: list[date] = []
    day = start
    while day < end:
        if calendar.is_trading_weekday(day):
            days.append(day)
        day += timedelta(days=1)
    return days


def roll_week_start(calendar: TradingCalendar, expiration: date, days: int) -> date:
    """Начало (понедельник) последней торговой недели, с которой до экспирации
    остаётся не менее ``days`` торговых дней (сама экспирация не считается)."""
    if days < 0:
        raise ValueError("Число торговых дней до ролла не может быть отрицательным")
    week = calendar.trading_week_start(expiration)
    for _ in range(520):
        if len(trading_days_between(calendar, week, expiration)) >= days:
            return week
        week -= timedelta(days=7)
    raise ValueError(f"Не найдена неделя ролла для экспирации {expiration}")


def roll_schedule(
    contracts: Sequence[ContractSpec], calendar: TradingCalendar, days: int
) -> list[Roll]:
    """Роллы между соседними по экспирации контрактами.

    Ролл с ``A`` на ``B`` — в начале недели, когда до экспирации ``A`` остаётся не
    менее ``days`` торговых дней. Ошибка, если роллы не идут строго по порядку.
    """
    ordered = sorted(contracts, key=lambda spec: spec.expiration)
    rolls: list[Roll] = []
    for current, following in zip(ordered, ordered[1:], strict=False):
        if current.expiration == following.expiration:
            raise ValueError(f"Две серии с экспирацией {current.expiration}")
        week = roll_week_start(calendar, current.expiration, days)
        bounds = calendar.trading_week_bounds(week)
        if bounds is None:
            raise ValueError(f"В неделе {week} нет торгов: ролл невозможен")
        if rolls and bounds[0] <= rolls[-1].at:
            raise ValueError(
                f"Ролл на {bounds[0]} не позже предыдущего ({rolls[-1].at}): "
                "серии слишком близки по экспирации"
            )
        rolls.append(Roll(current.contract_id, following.contract_id, week, bounds[0]))
    return rolls


def pick_ratio_basis(
    from_closes: Mapping[date, Decimal], to_closes: Mapping[date, Decimal]
) -> RatioBasis | None:
    """Последний общий торговый день двух контрактов и их закрытия."""
    common = from_closes.keys() & to_closes.keys()
    if not common:
        return None
    day = max(common)
    if from_closes[day] <= 0 or to_closes[day] <= 0:
        return None
    return RatioBasis(day, from_closes[day], to_closes[day])


def build_segments(
    order: Sequence[int], applied: Sequence[tuple[Roll, Decimal]]
) -> list[Segment]:
    """Сегменты continuous-серии.

    ``order`` — контракты по экспирации; ``applied`` — роллы с известным ratio
    по порядку. Ролл без ratio (данных ещё нет) и все следующие за ним не
    применяются: серия остаётся на прежнем контракте.
    """
    contracts = list(order)
    if not contracts:
        return []
    chain = [contracts[0]]
    for roll, _ in applied:
        if roll.from_contract_id != chain[-1]:
            raise ValueError("Роллы не образуют цепочку контрактов")
        chain.append(roll.to_contract_id)
    factor = Decimal(1)
    factors = [Decimal(1)] * len(chain)
    for index in range(len(applied) - 1, -1, -1):
        factor *= applied[index][1]
        factors[index] = factor
    segments: list[Segment] = []
    for index, contract_id in enumerate(chain):
        start = applied[index - 1][0].at if index > 0 else None
        end = applied[index][0].at if index < len(applied) else None
        segments.append(Segment(contract_id, start, end, factors[index]))
    return segments


def segment_at(segments: Sequence[Segment], moment: datetime) -> Segment | None:
    for segment in segments:
        if (segment.start is None or moment >= segment.start) and (
            segment.end is None or moment < segment.end
        ):
            return segment
    return None


def to_current_scale(price: Decimal, segment: Segment) -> Decimal:
    """Цена контракта сегмента в масштабе текущего контракта (для отображения)."""
    return price * segment.factor


def to_contract_scale(price: Decimal, segment: Segment) -> Decimal:
    """Обратное: цена continuous-серии в ценах контракта сегмента."""
    return price / segment.factor


def adjust_bar(bar: Bar, segment: Segment) -> ContinuousBar:
    """Бар с ценами в масштабе текущего контракта; объём не масштабируется."""
    factor = segment.factor
    if factor == 1:
        return ContinuousBar(bar, segment.contract_id, factor)
    return ContinuousBar(
        replace(
            bar,
            open=bar.open * factor,
            high=bar.high * factor,
            low=bar.low * factor,
            close=bar.close * factor,
        ),
        segment.contract_id,
        factor,
    )
