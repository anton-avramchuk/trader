"""Агрегация свечей по сетке биржи."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trader_importer.aggregate import aggregate, bucket_start
from trader_importer.models import Candle

MSK = ZoneInfo("Europe/Moscow")


def candle(minute_msk: int, o: str, h: str, low: str, c: str, v: str = "1") -> Candle:
    """Минутная свеча: ``minute_msk`` — минут от 10:00 МСК 2026-09-28."""
    start = datetime(2026, 9, 28, 10, tzinfo=MSK) + timedelta(minutes=minute_msk)
    return Candle(
        t=start.astimezone(UTC),
        o=Decimal(o),
        h=Decimal(h),
        l=Decimal(low),
        c=Decimal(c),
        v=Decimal(v),
    )


def test_bucket_grid_follows_exchange_midnight() -> None:
    moment = datetime(2026, 9, 28, 10, 22, tzinfo=MSK)

    assert bucket_start(moment, 15, MSK) == datetime(2026, 9, 28, 10, 15, tzinfo=MSK)
    assert bucket_start(moment, 240, MSK) == datetime(2026, 9, 28, 8, tzinfo=MSK)
    assert bucket_start(moment, 60, MSK) == datetime(2026, 9, 28, 10, tzinfo=MSK)
    # результат — всегда UTC
    assert bucket_start(moment, 15, MSK).utcoffset() == timedelta(0)


def test_ohlcv_of_a_bucket() -> None:
    rows = [
        candle(0, "10", "12", "9", "11", "5"),
        candle(1, "11", "15", "10", "14", "3"),
        candle(14, "14", "14", "8", "9", "2"),
        candle(15, "9", "9.5", "9", "9.2", "7"),
    ]

    first, second = aggregate(rows, 15, MSK)

    assert (first.o, first.h, first.l, first.c, first.v) == (
        Decimal("10"),
        Decimal("15"),
        Decimal("8"),
        Decimal("9"),
        Decimal("10"),
    )
    assert second.t == datetime(2026, 9, 28, 10, 15, tzinfo=MSK)
    assert second.v == Decimal("7")


def test_gaps_do_not_create_empty_buckets_and_empty_input() -> None:
    rows = [candle(0, "1", "1", "1", "1"), candle(60, "2", "2", "2", "2")]

    result = aggregate(rows, 15, MSK)

    assert [c.t for c in result] == [
        datetime(2026, 9, 28, 10, 0, tzinfo=MSK),
        datetime(2026, 9, 28, 11, 0, tzinfo=MSK),
    ]
    assert aggregate([], 15, MSK) == []


@given(st.lists(st.integers(0, 600), min_size=1, max_size=80, unique=True))
def test_aggregation_preserves_volume_and_extremes(minutes: list[int]) -> None:
    rows = [
        candle(
            m, str(10 + m % 7), str(20 + m % 5), str(1 + m % 3), str(10 + m % 4), "2"
        )
        for m in sorted(minutes)
    ]

    result = aggregate(rows, 15, MSK)

    assert sum(c.v for c in result) == sum(c.v for c in rows)
    assert max(c.h for c in result) == max(c.h for c in rows)
    assert min(c.l for c in result) == min(c.l for c in rows)
    assert [c.t for c in result] == sorted({c.t for c in result})
    for bucket in result:
        assert bucket.l <= min(bucket.o, bucket.c) and bucket.h >= max(
            bucket.o, bucket.c
        )


@pytest.mark.parametrize("minutes", [15, 60, 240])
def test_buckets_never_overlap(minutes: int) -> None:
    rows = [candle(m, "1", "2", "1", "2") for m in range(0, 500, 3)]

    starts = [c.t for c in aggregate(rows, minutes, MSK)]

    gaps = [b - a for a, b in zip(starts, starts[1:], strict=False)]
    assert all(g >= timedelta(minutes=minutes) for g in gaps)
