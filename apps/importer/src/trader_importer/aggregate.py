"""Агрегация свечей в более крупные интервалы по часам биржи."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from trader_importer.models import Candle


def bucket_start(moment: datetime, minutes: int, zone: ZoneInfo) -> datetime:
    """Начало интервала в ``minutes`` минут, выровненного по полуночи биржи (UTC).

    Сетка начинается с локальной полуночи дня, поэтому 15-минутные границы
    совпадают с часами биржи (10:00, 10:15…), а 4-часовые — 00:00, 04:00, 08:00…
    """
    local = moment.astimezone(zone)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = int((local - midnight).total_seconds() // 60)
    return (midnight + timedelta(minutes=elapsed // minutes * minutes)).astimezone(UTC)


def aggregate(candles: Sequence[Candle], minutes: int, zone: ZoneInfo) -> list[Candle]:
    """Сворачивает свечи по возрастанию времени в интервалы ``minutes`` минут."""
    result: list[Candle] = []
    current: datetime | None = None
    open_ = high = low = close = volume = Decimal(0)
    for candle in candles:
        start = bucket_start(candle.t, minutes, zone)
        if start != current:
            if current is not None:
                result.append(
                    Candle(t=current, o=open_, h=high, l=low, c=close, v=volume)
                )
            current = start
            open_, high, low, close, volume = (
                candle.o,
                candle.h,
                candle.l,
                candle.c,
                Decimal(0),
            )
        high = max(high, candle.h)
        low = min(low, candle.l)
        close = candle.c
        volume += candle.v
    if current is not None:
        result.append(Candle(t=current, o=open_, h=high, l=low, c=close, v=volume))
    return result
