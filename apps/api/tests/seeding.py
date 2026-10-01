"""Общие тестовые данные API: инструмент и свечи всех таймфреймов (ADR-0028)."""

import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from trader_db import CandleRow, create_instrument, make_engine, upsert_candles
from trader_engine.timeframes import TIMEFRAMES

MSK = ZoneInfo("Europe/Moscow")
# две торговые недели с разрывом: 28–29 сентября и 5–6 октября 2026
DAYS = [date(2026, 9, 28), date(2026, 9, 29), date(2026, 10, 5), date(2026, 10, 6)]
BARS_PER_DAY = 36  # 15-минутные свечи с 10:00 до 19:00 по Москве
TICKER = "SBER"


@dataclass
class Seed:
    id: int
    ticker: str


def _day_candles(day: date, start_price: float, rng: random.Random) -> list[CandleRow]:
    rows: list[CandleRow] = []
    price = start_price
    opened = datetime(day.year, day.month, day.day, 10, tzinfo=MSK)
    for i in range(BARS_PER_DAY):
        wave = 0.6 if (i // 9) % 2 == 0 else -0.6
        close = price + wave * rng.uniform(0.2, 1.0) + rng.gauss(0, 0.5)
        high = max(price, close) + rng.uniform(0, 0.6)
        low = min(price, close) - rng.uniform(0, 0.6)
        start = (opened + timedelta(minutes=15 * i)).astimezone(UTC)
        rows.append(
            CandleRow(
                open_time=start,
                close_time=start + timedelta(minutes=15),
                open=Decimal(f"{price:.4f}"),
                high=Decimal(f"{high:.4f}"),
                low=Decimal(f"{low:.4f}"),
                close=Decimal(f"{close:.4f}"),
                volume=Decimal(rng.randint(10, 500)),
                trading_day=day,
            )
        )
        price = close
    return rows


def _bucket(moment: datetime, timeframe: str) -> datetime:
    local = moment.astimezone(MSK)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if timeframe == "1h":
        start = local.replace(minute=0, second=0, microsecond=0)
    elif timeframe == "4h":
        start = midnight + timedelta(hours=local.hour // 4 * 4)
    elif timeframe == "1d":
        start = midnight
    else:  # 1w — понедельник
        start = midnight - timedelta(days=local.weekday())
    return start.astimezone(UTC)


def _aggregate(rows: list[CandleRow], timeframe: str) -> list[CandleRow]:
    length = TIMEFRAMES[timeframe]
    buckets: dict[datetime, list[CandleRow]] = {}
    for row in rows:
        buckets.setdefault(_bucket(row.open_time, timeframe), []).append(row)
    result: list[CandleRow] = []
    for start in sorted(buckets):
        group = buckets[start]
        result.append(
            CandleRow(
                open_time=start,
                close_time=start + length,
                open=group[0].open,
                high=max(r.high for r in group),
                low=min(r.low for r in group),
                close=group[-1].close,
                volume=sum((r.volume for r in group), Decimal(0)),
                trading_day=start.astimezone(MSK).date(),
            )
        )
    return result


def make_candles() -> dict[str, list[CandleRow]]:
    """Свечи всех таймфреймов, согласованные между собой (агрегат от 15m)."""
    rng = random.Random(20260928)
    base: list[CandleRow] = []
    price = 100.0
    for day in DAYS:
        rows = _day_candles(day, price, rng)
        base += rows
        price = float(rows[-1].close)
    return {"15m": base} | {
        tf: _aggregate(base, tf) for tf in TIMEFRAMES if tf != "15m"
    }


def seed_database(client: TestClient, database_url: str) -> Seed:
    """Инструмент SBER с синтетическими свечами 15m, 1h, 4h, 1d, 1w."""
    del client  # приложение уже мигрировало БД; данные кладём напрямую
    engine = make_engine(database_url)
    with Session(engine) as session:
        instrument = create_instrument(
            session,
            ticker=TICKER,
            name="Сбербанк",
            currency="RUB",
            tick_size=Decimal("0.01"),
            timezone="Europe/Moscow",
            source="test",
            tick_value=Decimal("1"),
        )
        for timeframe, rows in make_candles().items():
            upsert_candles(session, instrument.id, timeframe, rows)
        session.commit()
        seed = Seed(instrument.id, TICKER)
    engine.dispose()
    return seed


def iso(moment: datetime) -> str:
    return moment.isoformat()


def get(client: TestClient, path: str, **params: Any) -> dict[str, Any]:
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()
