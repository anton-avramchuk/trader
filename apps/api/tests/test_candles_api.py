"""REST свечей: диапазоны, пагинация, snapshot as-of (нужен TRADER_DATABASE_URL)."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from trader_engine.timeframes import TIMEFRAMES

from tests.seeding import BARS_PER_DAY, DAYS, Seed, get, iso


class TestCandles:
    def test_candles_are_ascending_closed_and_complete(
        self, client: TestClient, seed: Seed
    ) -> None:
        body = get(client, "/candles", instrument_id=seed.id, timeframe="15m")

        stamps = [c["timestamp"] for c in body["candles"]]
        assert stamps == sorted(set(stamps))
        assert body["count"] == len(stamps) == BARS_PER_DAY * len(DAYS)
        assert body["truncated"] is False and body["next_start"] is None
        first = body["candles"][0]
        assert set(first) == {
            "timestamp",
            "close_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "trading_day",
        }
        opened = datetime.fromisoformat(first["timestamp"])
        assert datetime.fromisoformat(first["close_time"]) == opened + timedelta(
            minutes=15
        )
        assert first["trading_day"] == DAYS[0].isoformat()
        assert Decimal(first["high"]) >= Decimal(first["low"])

    @pytest.mark.parametrize("timeframe", list(TIMEFRAMES))
    def test_every_timeframe_is_served(
        self, client: TestClient, seed: Seed, timeframe: str
    ) -> None:
        body = get(client, "/candles", instrument_id=seed.id, timeframe=timeframe)

        assert body["count"] > 0 and body["timeframe"] == timeframe
        assert body["instrument_id"] == seed.id

    def test_higher_timeframes_aggregate_the_lower_ones(
        self, client: TestClient, seed: Seed
    ) -> None:
        daily = get(client, "/candles", instrument_id=seed.id, timeframe="1d")
        quarters = get(client, "/candles", instrument_id=seed.id, timeframe="15m")

        assert daily["count"] == len(DAYS)
        total = sum(Decimal(c["volume"]) for c in quarters["candles"])
        assert sum(Decimal(c["volume"]) for c in daily["candles"]) == total

    def test_pagination_reassembles_the_full_series(
        self, client: TestClient, seed: Seed
    ) -> None:
        full = get(client, "/candles", instrument_id=seed.id, timeframe="15m")
        collected: list[dict[str, Any]] = []
        start: str | None = None
        for _ in range(100):
            params: dict[str, Any] = {
                "instrument_id": seed.id,
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

    def test_tail_returns_the_last_candles_in_ascending_order(
        self, client: TestClient, seed: Seed
    ) -> None:
        full = get(client, "/candles", instrument_id=seed.id, timeframe="15m")

        tail = get(
            client,
            "/candles",
            instrument_id=seed.id,
            timeframe="15m",
            limit=5,
            tail=True,
        )

        assert tail["candles"] == full["candles"][-5:]
        assert tail["truncated"] is True and tail["next_start"] is None
        older = get(
            client,
            "/candles",
            instrument_id=seed.id,
            timeframe="15m",
            limit=5,
            tail=True,
            end=tail["candles"][0]["timestamp"],
        )
        assert older["candles"] == full["candles"][-10:-5]

    def test_start_and_end_bound_the_range(
        self, client: TestClient, seed: Seed
    ) -> None:
        full = get(client, "/candles", instrument_id=seed.id, timeframe="15m")
        lo, hi = full["candles"][10]["timestamp"], full["candles"][20]["timestamp"]

        body = get(
            client, "/candles", instrument_id=seed.id, timeframe="15m", start=lo, end=hi
        )

        assert body["candles"] == full["candles"][10:20]

    def test_errors(self, client: TestClient, seed: Seed) -> None:
        assert client.get("/candles", params={"timeframe": "15m"}).status_code == 422
        assert (
            client.get(
                "/candles", params={"instrument_id": 99999, "timeframe": "15m"}
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/candles", params={"instrument_id": seed.id, "timeframe": "1m"}
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/candles",
                params={"instrument_id": seed.id, "timeframe": "15m", "limit": 0},
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/candles",
                params={
                    "instrument_id": seed.id,
                    "timeframe": "15m",
                    "start": "2026-09-28T10:00:00",
                },
            ).status_code
            == 422
        )


class TestSnapshot:
    def test_hides_candles_closed_after_as_of(
        self, client: TestClient, seed: Seed
    ) -> None:
        full = get(client, "/candles", instrument_id=seed.id, timeframe="15m")
        moment = datetime.fromisoformat(full["candles"][20]["close_time"])

        body = get(
            client,
            "/snapshot",
            instrument_id=seed.id,
            timeframe="15m",
            as_of=iso(moment),
        )

        assert body["count"] == 21 and body["as_of"] is not None
        assert all(
            datetime.fromisoformat(c["close_time"]) <= moment for c in body["candles"]
        )
        assert body["candles"] == full["candles"][:21]

    def test_before_the_first_close_is_empty_and_far_future_is_everything(
        self, client: TestClient, seed: Seed
    ) -> None:
        full = get(client, "/candles", instrument_id=seed.id, timeframe="15m")
        first_open = datetime.fromisoformat(full["candles"][0]["timestamp"])

        early = get(
            client,
            "/snapshot",
            instrument_id=seed.id,
            timeframe="15m",
            as_of=iso(first_open),
        )
        late = get(
            client,
            "/snapshot",
            instrument_id=seed.id,
            timeframe="15m",
            as_of="2030-01-01T00:00:00Z",
        )

        assert early["candles"] == []
        assert late["candles"] == full["candles"]

    def test_limit_keeps_the_latest_candles(
        self, client: TestClient, seed: Seed
    ) -> None:
        full = get(client, "/candles", instrument_id=seed.id, timeframe="15m")

        body = get(
            client,
            "/snapshot",
            instrument_id=seed.id,
            timeframe="15m",
            as_of="2030-01-01T00:00:00Z",
            limit=4,
        )

        assert body["candles"] == full["candles"][-4:] and body["truncated"] is True

    def test_requires_a_timezone_aware_moment(
        self, client: TestClient, seed: Seed
    ) -> None:
        response = client.get(
            "/snapshot",
            params={
                "instrument_id": seed.id,
                "timeframe": "15m",
                "as_of": "2026-09-28T12:00:00",
            },
        )

        assert response.status_code == 422
