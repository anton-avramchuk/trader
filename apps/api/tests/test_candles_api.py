"""REST свечей: диапазоны, continuous, snapshot as-of (нужен TRADER_DATABASE_URL)."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
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


@pytest.fixture
def seed(client: TestClient, database_url: str) -> Seed:
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


class TestCandles:
    def test_contract_bars_are_ascending_and_in_contract_prices(
        self, client: TestClient, seed: Seed
    ) -> None:
        body = get(client, "/candles", contract_id=seed.b, timeframe="15m")

        stamps = [c["timestamp"] for c in body["candles"]]
        assert stamps == sorted(set(stamps)) and body["count"] == len(stamps) > 10
        assert body["truncated"] is False and body["next_start"] is None
        assert body["rolls"] == [] and body["dataset_version_id"] is None
        first = body["candles"][0]
        assert first["contract_id"] is None and first["price_factor"] is None
        assert Decimal(first["open"]) >= 200  # цены B — свои, без масштаба

    def test_pagination_reassembles_the_full_series(
        self, client: TestClient, seed: Seed
    ) -> None:
        full = get(client, "/candles", contract_id=seed.a, timeframe="15m")
        collected: list[dict[str, Any]] = []
        start: str | None = None
        for _ in range(100):
            params: dict[str, Any] = {
                "contract_id": seed.a,
                "timeframe": "15m",
                "limit": 7,
            }
            if start:
                params["start"] = start
            page = get(client, "/candles", **params)
            collected += page["candles"]
            if not page["truncated"]:
                break
            start = page["next_start"]
        assert collected == full["candles"]

    def test_tail_returns_the_latest_bars_and_signals_older_ones(
        self, client: TestClient, seed: Seed
    ) -> None:
        full = get(client, "/candles", contract_id=seed.a, timeframe="15m")

        latest = get(
            client, "/candles", contract_id=seed.a, timeframe="15m", limit=4, tail=True
        )
        older = get(
            client,
            "/candles",
            contract_id=seed.a,
            timeframe="15m",
            limit=4,
            tail=True,
            end=latest["candles"][0]["timestamp"],
        )

        assert latest["candles"] == full["candles"][-4:]
        assert latest["truncated"] is True and latest["next_start"] is None
        assert older["candles"] == full["candles"][-8:-4]

    def test_range_filter(self, client: TestClient, seed: Seed) -> None:
        body = get(
            client,
            "/candles",
            contract_id=seed.a,
            timeframe="1d",
            start=iso(seed.roll_at),
        )

        assert body["count"] == 2  # 05.10 и 06.10
        stamps = [datetime.fromisoformat(c["timestamp"]) for c in body["candles"]]
        assert all(stamp >= seed.roll_at for stamp in stamps)

    def test_raw_minutes_use_the_latest_or_a_chosen_dataset_version(
        self, client: TestClient, seed: Seed
    ) -> None:
        latest = get(client, "/candles", contract_id=seed.a, timeframe="1m", limit=5)

        assert latest["dataset_version_id"] is not None and latest["count"] == 5
        assert latest["candles"][0]["close_time"] > latest["candles"][0]["timestamp"]
        same = get(
            client,
            "/candles",
            contract_id=seed.a,
            timeframe="1m",
            limit=5,
            dataset_version_id=latest["dataset_version_id"],
        )
        assert same == latest
        assert (
            client.get(
                "/candles",
                params={
                    "contract_id": seed.a,
                    "timeframe": "1m",
                    "dataset_version_id": 999,
                },
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/candles",
                params={
                    "contract_id": seed.a,
                    "timeframe": "15m",
                    "dataset_version_id": latest["dataset_version_id"],
                },
            ).status_code
            == 409
        )

    def test_continuous_switches_contract_at_the_roll_with_ratio(
        self, client: TestClient, seed: Seed
    ) -> None:
        body = get(client, "/candles", root_id=seed.root_id, timeframe="1d")

        by_contract = {(c["contract_id"], c["price_factor"]) for c in body["candles"]}
        assert by_contract == {(seed.a, "2.000000000000"), (seed.b, "1")}
        assert [r["ratio"] for r in body["rolls"]] == ["2.000000000000"]
        roll = body["rolls"][0]
        assert (roll["from_contract_id"], roll["to_contract_id"]) == (seed.a, seed.b)
        for candle in body["candles"]:
            moment = datetime.fromisoformat(candle["timestamp"])
            expected = seed.a if moment < seed.roll_at else seed.b
            assert candle["contract_id"] == expected
        assert Decimal(body["candles"][0]["close"]) >= 200  # A приведён к масштабу B

    def test_weekly_bars_never_mix_contracts(
        self, client: TestClient, seed: Seed
    ) -> None:
        body = get(client, "/candles", root_id=seed.root_id, timeframe="1w")

        assert len(body["candles"]) == 2
        assert [c["contract_id"] for c in body["candles"]] == [seed.a, seed.b]


class TestValidation:
    @pytest.mark.parametrize(
        "params",
        [
            {"timeframe": "15m"},
            {"timeframe": "15m", "root_id": 1, "contract_id": 1},
            {"timeframe": "7m", "contract_id": 1},
            {"timeframe": "1m", "root_id": 1},
            {"timeframe": "15m", "contract_id": 1, "start": "2026-10-05T00:00:00"},
            {"timeframe": "15m", "contract_id": 1, "limit": 0},
        ],
    )
    def test_bad_requests_are_422(
        self, client: TestClient, seed: Seed, params: dict[str, Any]
    ) -> None:
        assert client.get("/candles", params=params).status_code == 422

    def test_unknown_ids_are_404(self, client: TestClient, seed: Seed) -> None:
        assert (
            client.get(
                "/candles", params={"timeframe": "1d", "root_id": 999}
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/candles", params={"timeframe": "1d", "contract_id": 999}
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/candles", params={"timeframe": "1m", "contract_id": 999}
            ).status_code
            == 404
        )

    def test_snapshot_requires_an_aware_as_of(
        self, client: TestClient, seed: Seed
    ) -> None:
        for as_of in (None, "2026-10-05T12:00:00"):
            params: dict[str, Any] = {"timeframe": "1d", "contract_id": seed.a}
            if as_of:
                params["as_of"] = as_of
            assert client.get("/snapshot", params=params).status_code == 422

    def test_contract_without_minutes_is_404(self, client: TestClient) -> None:
        root = client.post(
            "/roots",
            json={
                "code": "BR",
                "name": "Brent",
                "quote_currency": "USD",
                "tick_size": "0.01",
            },
        ).json()
        contract = client.post(
            f"/roots/{root['id']}/contracts", json={"expiration_date": "2026-12-01"}
        ).json()

        response = client.get(
            "/candles", params={"timeframe": "1m", "contract_id": contract["id"]}
        )

        assert response.status_code == 404


class TestSnapshot:
    @pytest.mark.parametrize("timeframe", ["1m", *TIMEFRAMES])
    @pytest.mark.parametrize(
        "as_of",
        [
            datetime(2026, 9, 28, 12, 7, tzinfo=UTC),
            datetime(2026, 9, 29, 9, 30, 30, tzinfo=UTC),
            datetime(2026, 10, 5, 10, 0, tzinfo=UTC),
            datetime(2026, 10, 6, 20, 59, tzinfo=UTC),
        ],
    )
    def test_no_candle_closes_after_as_of(
        self, client: TestClient, seed: Seed, timeframe: str, as_of: datetime
    ) -> None:
        selections: list[dict[str, Any]] = [{"contract_id": seed.a}]
        if timeframe != "1m":
            selections.append({"root_id": seed.root_id})
        for selection in selections:
            body = get(
                client, "/snapshot", timeframe=timeframe, as_of=iso(as_of), **selection
            )

            assert body["as_of"] is not None
            closes = [datetime.fromisoformat(c["close_time"]) for c in body["candles"]]
            assert all(close <= as_of for close in closes)

    def test_returns_the_latest_bars_closed_by_as_of(
        self, client: TestClient, seed: Seed
    ) -> None:
        as_of = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
        everything = get(
            client,
            "/snapshot",
            contract_id=seed.a,
            timeframe="15m",
            as_of=iso(as_of),
            limit=10_000,
        )

        latest = get(
            client,
            "/snapshot",
            contract_id=seed.a,
            timeframe="15m",
            as_of=iso(as_of),
            limit=3,
        )

        assert latest["truncated"] is True and latest["next_start"] is None
        assert latest["candles"] == everything["candles"][-3:]
        last_close = datetime.fromisoformat(everything["candles"][-1]["close_time"])
        assert last_close <= as_of

    def test_a_roll_is_invisible_before_it_becomes_available(
        self, client: TestClient, seed: Seed
    ) -> None:
        before = get(
            client,
            "/snapshot",
            root_id=seed.root_id,
            timeframe="1d",
            as_of=iso(seed.roll_at - MINUTE),
        )
        after = get(
            client,
            "/snapshot",
            root_id=seed.root_id,
            timeframe="1d",
            as_of=iso(seed.roll_at + timedelta(days=5)),
        )

        assert before["rolls"] == [] and after["rolls"] != []
        assert {c["price_factor"] for c in before["candles"]} == {"1"}
        assert {c["contract_id"] for c in before["candles"]} == {seed.a}
        old = {c["timestamp"]: c for c in after["candles"]}
        for candle in before["candles"]:
            # Тот же бар в «будущем» масштабе умножен на ratio ролла.
            scaled = Decimal(candle["close"]) * FACTOR
            assert Decimal(old[candle["timestamp"]]["close"]) == scaled
