"""Регрессия на реальных данных ISS: импорт → бары → ролл → API (нужен БД).

Датасет ``tests/fixtures/ng_2026_03.csv.gz``: NGH6 и NGJ6, 09–31.03.2026 — внутри
ролл H → J (неделя с 16.03 при N = 5) и смена режима сессий 23.03.2026.
Ожидаемые результаты лежат рядом (``ng_2026_03.expected.json``); при расхождении
pytest показывает diff словарей. Осознанно обновить ожидания:
``UPDATE_REGRESSION=1 pytest tests/test_regression_dataset.py``.
"""

import csv
import gzip
import hashlib
import json
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from trader_db import (
    build_bars,
    load_contract_calendar,
    make_engine,
    read_bars,
    run_candle_import,
    update_rolls,
)
from trader_db.models import Contract
from trader_engine.aggregation import TIMEFRAMES
from trader_engine.ingest import RawRow

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
DATASET = FIXTURES / "ng_2026_03.csv.gz"
EXPECTED = FIXTURES / "ng_2026_03.expected.json"
EXPIRATIONS = {"NGH6": "2026-03-27", "NGJ6": "2026-04-28"}
COMPLETE_UNTIL = datetime(2026, 4, 1, tzinfo=UTC)
ROLL_WEEK = date(2026, 3, 16)
REGIME_CHANGE = date(2026, 3, 23)


def dataset_rows() -> dict[str, list[RawRow]]:
    rows: dict[str, list[RawRow]] = {secid: [] for secid in EXPIRATIONS}
    with gzip.open(DATASET, "rt", encoding="utf-8", newline="") as handle:
        for number, record in enumerate(csv.DictReader(handle), start=1):
            rows[record["secid"]].append(
                RawRow(
                    row_number=number,
                    timestamp=datetime.fromisoformat(record["timestamp"]),
                    open=Decimal(record["open"]),
                    high=Decimal(record["high"]),
                    low=Decimal(record["low"]),
                    close=Decimal(record["close"]),
                    volume=Decimal(record["volume"]),
                    trade_count=int(record["trade_count"])
                    if record["trade_count"]
                    else None,
                )
            )
    return rows


def digest(rows: list[list[str]]) -> str:
    text = "\n".join(",".join(row) for row in rows)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def loaded(client: TestClient, database_url: str) -> dict[str, Any]:
    """Root NG, два контракта, импорт, бары всех TF и ролл; возвращает сводки."""
    root = client.post(
        "/roots",
        json={
            "code": "NG",
            "name": "Natural gas",
            "quote_currency": "USD",
            "tick_size": "0.001",
            "roll_trading_days": 5,
        },
    ).json()
    engine = make_engine(database_url)
    factory = sessionmaker(engine, expire_on_commit=False)
    ids: dict[str, int] = {}
    for secid, expiration in EXPIRATIONS.items():
        ids[secid] = client.post(
            f"/roots/{root['id']}/contracts",
            json={"expiration_date": expiration, "secid": secid},
        ).json()["id"]

    imports: dict[str, Any] = {}
    for secid, rows in dataset_rows().items():
        with factory() as session:
            calendar = load_contract_calendar(session, ids[secid])
        outcome = run_candle_import(
            factory,
            provider_code="moex_iss",
            contract_id=ids[secid],
            source_type="api",
            source_name=secid,
            rows=rows,
            calendar=calendar,
        )
        report = outcome.report
        imports[secid] = {
            "rows_total": report["rows_total"],
            "inserted": report["inserted"],
            "duplicates": report["duplicates"],
            "conflicts": report["conflicts"],
            "rows_rejected": report["rows_rejected"],
            "outside_session_rows": report["warnings"]["outside_session_rows"],
            "missing_intervals": report["missing"]["intervals"],
            "missing_minutes": report["missing"]["minutes"],
            "range": report["range"],
        }

    bars: dict[str, Any] = {}
    with factory() as session:
        for secid in EXPIRATIONS:
            calendar = load_contract_calendar(session, ids[secid])
            bars[secid] = {}
            for timeframe in TIMEFRAMES:
                build_bars(
                    session,
                    ids[secid],
                    timeframe,
                    calendar,
                    include_weekend_sessions=False,
                    complete_until=COMPLETE_UNTIL,
                )
                stored = read_bars(session, ids[secid], timeframe)
                bars[secid][timeframe] = {
                    "count": len(stored),
                    "first": stamp(stored[0].timestamp),
                    "last_close": stamp(stored[-1].close_time),
                    "partial": sum(bar.is_partial for bar in stored),
                    "sha256": digest(
                        [
                            [
                                stamp(bar.timestamp),
                                stamp(bar.close_time),
                                str(bar.open),
                                str(bar.high),
                                str(bar.low),
                                str(bar.close),
                                str(bar.volume),
                                str(bar.candles),
                                str(bar.is_partial),
                            ]
                            for bar in stored
                        ]
                    ),
                }
        calendar = load_contract_calendar(session, ids["NGH6"])
        events = update_rolls(session, root["id"], calendar)
        rolls = [
            {
                "from": next(s for s, i in ids.items() if i == e.from_contract_id),
                "to": next(s for s, i in ids.items() if i == e.to_contract_id),
                "rolled_at": stamp(e.rolled_at),
                "ratio": str(e.ratio),
                "basis_trading_day": e.basis_trading_day.isoformat(),
                "from_close": str(e.from_close),
                "to_close": str(e.to_close),
            }
            for e in events
        ]
        session.commit()
        secids = {c.id: c.secid for c in session.scalars(select(Contract)) if c.secid}
    engine.dispose()
    return {
        "root_id": root["id"],
        "ids": ids,
        "secids": secids,
        "imports": imports,
        "bars": bars,
        "rolls": rolls,
    }


def continuous_rows(
    client: TestClient, root_id: int, timeframe: str, secids: dict[int, str]
) -> list[list[str]]:
    body = client.get(
        "/candles", params={"root_id": root_id, "timeframe": timeframe}
    ).json()
    return [
        [
            candle["timestamp"],
            secids[candle["contract_id"]],
            candle["price_factor"],
            candle["open"],
            candle["high"],
            candle["low"],
            candle["close"],
            candle["volume"],
        ]
        for candle in body["candles"]
    ]


def snapshot_summary(
    client: TestClient, root_id: int, secids: dict[int, str], as_of: str
) -> list[list[str]]:
    body = client.get(
        "/snapshot",
        params={"root_id": root_id, "timeframe": "1d", "as_of": as_of, "limit": 3},
    ).json()
    return [
        [c["timestamp"], secids[c["contract_id"]], c["price_factor"], c["close"]]
        for c in body["candles"]
    ]


def summarize(client: TestClient, loaded: dict[str, Any]) -> dict[str, Any]:
    root_id, secids = loaded["root_id"], loaded["secids"]
    return {
        "imports": loaded["imports"],
        "bars": loaded["bars"],
        "rolls": loaded["rolls"],
        "continuous_1w": continuous_rows(client, root_id, "1w", secids),
        "continuous_1d": continuous_rows(client, root_id, "1d", secids),
        "snapshots_1d": {
            as_of: snapshot_summary(client, root_id, secids, as_of)
            for as_of in (
                "2026-03-13T12:00:00Z",
                "2026-03-19T12:00:00Z",
                "2026-03-25T12:00:00Z",
            )
        },
    }


def test_results_match_the_recorded_expectations(
    client: TestClient, loaded: dict[str, Any]
) -> None:
    actual = json.loads(json.dumps(summarize(client, loaded)))

    if os.environ.get("UPDATE_REGRESSION"):
        EXPECTED.write_text(
            json.dumps(actual, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        pytest.skip("ожидания обновлены")
    assert EXPECTED.exists(), "нет ожиданий: UPDATE_REGRESSION=1 pytest ..."
    assert actual == json.loads(EXPECTED.read_text(encoding="utf-8"))


def test_real_data_fits_the_calendar(loaded: dict[str, Any]) -> None:
    """Ни одна реальная свеча не лежит вне сессий календаря (калибровка ADR-0014)."""
    for secid, report in loaded["imports"].items():
        assert report["outside_session_rows"] == 0, secid
        assert report["rows_rejected"] == 0 and report["conflicts"] == 0, secid


def test_roll_happens_at_the_start_of_the_expected_week(loaded: dict[str, Any]) -> None:
    assert len(loaded["rolls"]) == 1
    roll = loaded["rolls"][0]
    assert (roll["from"], roll["to"]) == ("NGH6", "NGJ6")
    rolled = datetime.fromisoformat(roll["rolled_at"])
    assert rolled.astimezone(UTC).date() >= date(2026, 3, 13)
    assert rolled.astimezone(UTC).date() <= ROLL_WEEK
    assert Decimal(roll["ratio"]) > 0


def test_no_weekly_or_daily_bar_mixes_contracts(
    client: TestClient, loaded: dict[str, Any]
) -> None:
    for timeframe in ("1w", "1d", "4h", "15m"):
        body = client.get(
            "/candles",
            params={
                "root_id": loaded["root_id"],
                "timeframe": timeframe,
                "limit": 20000,
            },
        ).json()
        roll = datetime.fromisoformat(loaded["rolls"][0]["rolled_at"])
        for candle in body["candles"]:
            closes = datetime.fromisoformat(candle["close_time"])
            assert (closes > roll) == (candle["contract_id"] == loaded["ids"]["NGJ6"])


def test_snapshot_never_returns_a_candle_closing_after_as_of(
    client: TestClient, loaded: dict[str, Any]
) -> None:
    for as_of in (
        "2026-03-13T12:00:00Z",
        "2026-03-20T15:31:00Z",
        "2026-03-27T09:00:00Z",
    ):
        limit = datetime.fromisoformat(as_of)
        for timeframe in ("15m", "1h", "4h", "1d", "1w"):
            body = client.get(
                "/snapshot",
                params={
                    "root_id": loaded["root_id"],
                    "timeframe": timeframe,
                    "as_of": as_of,
                    "limit": 20000,
                },
            ).json()
            assert body["candles"] or timeframe == "1w"  # первая неделя ещё не закрыта
            assert all(
                datetime.fromisoformat(c["close_time"]) <= limit
                for c in body["candles"]
            ), (timeframe, as_of)


def test_regime_change_week_is_present_in_daily_bars(
    client: TestClient, loaded: dict[str, Any]
) -> None:
    body = client.get(
        "/candles",
        params={"contract_id": loaded["ids"]["NGJ6"], "timeframe": "1d"},
    ).json()

    days = {candle["trading_day"] for candle in body["candles"]}
    assert REGIME_CHANGE.isoformat() in days
    assert "2026-03-20" in days
