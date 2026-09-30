"""Общие тестовые данные API: root с двумя контрактами, барами и роллом."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from trader_db import (
    build_bars,
    extend_dataset_version,
    finish_import,
    insert_candles,
    load_trading_calendar,
    make_engine,
    start_import,
    update_rolls,
)
from trader_engine.aggregation import TIMEFRAMES
from trader_engine.calendar import TradingCalendar, moex_forts_calendar
from trader_engine.ingest import Candle1m

CALENDAR = moex_forts_calendar()
BEFORE = [date(2026, 9, 28), date(2026, 9, 29)]
AFTER = [date(2026, 10, 5), date(2026, 10, 6)]
FAR = datetime(2030, 1, 1, tzinfo=UTC)
FACTOR = Decimal(2)
MINUTE = timedelta(minutes=1)


def day_candles(day: date, factor: Decimal) -> list[Candle1m]:
    candles: list[Candle1m] = []
    index = 0
    for session in CALENDAR.sessions_on(day):
        moment = session.start
        while moment < session.end:
            if index % 7 == 0:
                base = Decimal(100 + index % 17)
                candles.append(
                    Candle1m(
                        timestamp=moment,
                        open=base * factor,
                        high=(base + 3) * factor,
                        low=(base - 2) * factor,
                        close=(base + 1) * factor,
                        volume=Decimal(index % 5 + 1),
                    )
                )
            index += 1
            moment += MINUTE
    return candles


@dataclass
class Seed:
    root_id: int
    a: int
    b: int
    roll_at: datetime


def load(session: Session, contract_id: int, candles: list[Candle1m]) -> None:
    import_id = start_import(
        session, provider_code="csv", contract_id=contract_id, source_type="file"
    )
    insert_candles(session, import_id, candles)
    finish_import(session, import_id)
    extend_dataset_version(session, import_id)


def seed_database(client: TestClient, database_url: str) -> Seed:
    """Root с двумя контрактами: B в два раза дороже A; ролл в неделю с 05.10."""
    root = client.post(
        "/roots",
        json={
            "code": "NG",
            "name": "Gas",
            "quote_currency": "USD",
            "tick_size": "0.001",
            "roll_trading_days": 5,
        },
    ).json()
    ids = [
        client.post(
            f"/roots/{root['id']}/contracts", json={"expiration_date": expiration}
        ).json()["id"]
        for expiration in ("2026-10-15", "2026-11-12")
    ]
    engine = make_engine(database_url)
    with Session(engine) as session:
        calendar: TradingCalendar = load_trading_calendar(session, "moex_forts")
        for contract_id, factor in zip(ids, (Decimal(1), FACTOR), strict=True):
            candles = [c for d in BEFORE + AFTER for c in day_candles(d, factor)]
            load(session, contract_id, candles)
            for timeframe in TIMEFRAMES:
                build_bars(
                    session,
                    contract_id,
                    timeframe,
                    calendar,
                    include_weekend_sessions=False,
                    complete_until=FAR,
                )
        events = update_rolls(session, root["id"], calendar)
        session.commit()
        roll_at = events[0].rolled_at
    engine.dispose()
    return Seed(root["id"], ids[0], ids[1], roll_at)


def iso(moment: datetime) -> str:
    return moment.isoformat()


def get(client: TestClient, path: str, **params: Any) -> dict[str, Any]:
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()
