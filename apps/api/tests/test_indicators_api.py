"""REST индикаторов: каталог, значения, MTF-проекция, as_of (нужен БД)."""

import json
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from trader_engine.indicators import BarInput, create, run

from tests.seeding import Seed, get, iso


def values(client: TestClient, indicator: str, **params: Any) -> dict[str, Any]:
    response = client.get(
        "/indicator-values", params={"indicator": indicator, **params}
    )
    assert response.status_code == 200, response.text
    return response.json()


def candle_bars(client: TestClient, timeframe: str, **selection: Any) -> list[BarInput]:
    body = get(client, "/candles", timeframe=timeframe, limit=20000, **selection)
    return [
        BarInput(
            timestamp=datetime.fromisoformat(c["timestamp"]),
            close_time=datetime.fromisoformat(c["close_time"]),
            open=float(c["open"]),
            high=float(c["high"]),
            low=float(c["low"]),
            close=float(c["close"]),
            volume=float(c["volume"]),
            trading_day=datetime.fromisoformat(c["timestamp"]).date(),
        )
        for c in body["candles"]
    ]


class TestCatalog:
    def test_lists_plugins_with_form_schema(self, client: TestClient) -> None:
        catalog = {i["name"]: i for i in client.get("/indicators").json()}

        assert {"sma", "ema", "rsi", "macd", "atr", "vwap", "obv"} <= set(catalog)
        sma = catalog["sma"]
        assert sma["defaults"] == {"period": 20} and sma["pane"] == "price"
        assert sma["params_schema"]["properties"]["period"]["minimum"] == 1
        assert catalog["macd"]["outputs"] == ["macd", "signal", "histogram"]
        assert catalog["rsi"]["pane"] == "separate"


class TestValues:
    def test_same_timeframe_matches_the_engine_and_marks_warmup(
        self, client: TestClient, seed: Seed
    ) -> None:
        body = values(
            client,
            "sma",
            contract_id=seed.a,
            chart_timeframe="15m",
            params=json.dumps({"period": 5}),
        )

        expected = run(
            create("sma", {"period": 5}), candle_bars(client, "15m", contract_id=seed.a)
        ).values["value"]
        got = [p["values"]["value"] for p in body["points"]]
        assert got == expected
        assert [p["valid"] for p in body["points"]][:6] == [False] * 4 + [True] * 2
        assert body["warmup_bars"] == 5 and body["params"] == {"period": 5}
        assert body["source_timeframe"] == "15m" and body["source_truncated"] is False

    def test_higher_timeframe_is_a_step_that_never_leaks(
        self, client: TestClient, seed: Seed
    ) -> None:
        body = values(
            client,
            "sma",
            contract_id=seed.a,
            chart_timeframe="15m",
            source_timeframe="1h",
            params=json.dumps({"period": 2}),
        )

        points = body["points"]
        chart = get(
            client, "/candles", contract_id=seed.a, timeframe="15m", limit=20000
        )
        closes = {c["timestamp"]: c["close_time"] for c in chart["candles"]}
        seen_valid = False
        for point in points:
            if point["available_at"] is None:
                assert not point["valid"]
                continue
            chart_close = datetime.fromisoformat(closes[point["timestamp"]])
            assert datetime.fromisoformat(point["available_at"]) <= chart_close
            seen_valid = seen_valid or point["valid"]
        assert seen_valid
        # Значения совпадают с расчётом по 1h-барам и держатся ступенькой.
        hourly = run(
            create("sma", {"period": 2}), candle_bars(client, "1h", contract_id=seed.a)
        ).values["value"]
        by_source = {
            p["source_timestamp"]: p["values"]["value"] for p in points if p["valid"]
        }
        hourly_bars = candle_bars(client, "1h", contract_id=seed.a)
        for index, bar in enumerate(hourly_bars):
            key = bar.timestamp.isoformat().replace("+00:00", "Z")
            if key in by_source and hourly[index] is not None:
                assert by_source[key] == pytest.approx(hourly[index])

    def test_lower_source_timeframe_is_rejected(
        self, client: TestClient, seed: Seed
    ) -> None:
        response = client.get(
            "/indicator-values",
            params={
                "indicator": "sma",
                "contract_id": seed.a,
                "chart_timeframe": "1h",
                "source_timeframe": "15m",
            },
        )

        assert response.status_code == 422 and "запрещён" in response.json()["detail"]

    @pytest.mark.parametrize(
        ("params", "status"),
        [
            ({"indicator": "nope"}, 404),
            ({"indicator": "sma", "params": "{bad"}, 422),
            ({"indicator": "sma", "params": "[]"}, 422),
            ({"indicator": "sma", "params": '{"period": 0}'}, 422),
            ({"indicator": "macd", "params": '{"fast": 30, "slow": 10}'}, 422),
            ({"indicator": "sma", "chart_timeframe": "1m"}, 422),
        ],
    )
    def test_bad_requests(
        self, client: TestClient, seed: Seed, params: dict[str, Any], status: int
    ) -> None:
        query = {"contract_id": seed.a, "chart_timeframe": "15m", **params}

        assert client.get("/indicator-values", params=query).status_code == status

    def test_selection_is_required(self, client: TestClient, seed: Seed) -> None:
        both = client.get(
            "/indicator-values",
            params={
                "indicator": "sma",
                "chart_timeframe": "15m",
                "root_id": seed.root_id,
                "contract_id": seed.a,
            },
        )

        assert both.status_code == 422
        assert (
            client.get(
                "/indicator-values",
                params={"indicator": "sma", "chart_timeframe": "15m"},
            ).status_code
            == 422
        )

    def test_continuous_series_supports_calendar_anchored_vwap(
        self, client: TestClient, seed: Seed
    ) -> None:
        body = values(client, "vwap", root_id=seed.root_id, chart_timeframe="1h")

        assert body["points"] and all(p["valid"] for p in body["points"])
        assert all(p["values"]["value"] > 0 for p in body["points"])

    def test_as_of_hides_bars_closed_later(
        self, client: TestClient, seed: Seed
    ) -> None:
        moment = datetime.fromisoformat(
            get(client, "/candles", contract_id=seed.a, timeframe="1h")["candles"][10][
                "close_time"
            ]
        )

        body = values(
            client,
            "ema",
            contract_id=seed.a,
            chart_timeframe="1h",
            params=json.dumps({"period": 3}),
            as_of=iso(moment),
        )

        assert len(body["points"]) == 11
        assert datetime.fromisoformat(body["points"][-1]["timestamp"]) < moment
        assert body["source_bar_count"] == 11

    def test_repeat_request_is_served_from_the_cache(
        self, client: TestClient, seed: Seed
    ) -> None:
        query = {"contract_id": seed.a, "chart_timeframe": "15m"}
        values(client, "sma", **query)

        values(client, "sma", **query)

        stats = client.app.state.indicator_cache.stats  # type: ignore[attr-defined]
        assert stats.misses == 1 and stats.hits >= 1

    def test_no_bars_gives_an_empty_answer(self, client: TestClient) -> None:
        root = client.post(
            "/roots",
            json={
                "code": "ZZ",
                "name": "Empty",
                "quote_currency": "USD",
                "tick_size": "0.01",
            },
        ).json()

        body = values(client, "sma", root_id=root["id"], chart_timeframe="1h")

        assert body["points"] == [] and body["source_bar_count"] == 0
