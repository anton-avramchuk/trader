"""REST профилей графика (нужен TRADER_DATABASE_URL)."""

from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session
from trader_db import create_instrument, make_engine
from trader_db.models import Instrument

CONFIG = {
    "chart_timeframe": "15m",
    "indicators": [
        {"name": "ema", "params": {"period": 200}, "source_timeframe": "1h"},
        {"name": "rsi", "params": {"period": 14}, "source_timeframe": "15m"},
    ],
    "layers": {"volume": True, "levels": True},
    "style": {"theme": "dark"},
}


def make_instrument(database_url: str, ticker: str = "NG") -> int:
    engine = make_engine(database_url)
    with Session(engine) as session:
        instrument = create_instrument(
            session,
            ticker=ticker,
            name=ticker,
            currency="RUB",
            tick_size=Decimal("0.01"),
            timezone="Europe/Moscow",
            source="test",
        )
        session.commit()
        instrument_id = instrument.id
    engine.dispose()
    return instrument_id


def create(client: TestClient, name: str = "Основной", **extra: Any) -> dict[str, Any]:
    response = client.post(
        "/chart-profiles", json={"name": name, "config": CONFIG, **extra}
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestCrud:
    def test_create_get_roundtrips_the_config(self, client: TestClient) -> None:
        created = create(client)

        assert created["instrument_id"] is None and created["last_used_at"] is None
        assert created["config"] == CONFIG | {"chart_timeframe": "15m"}
        assert client.get(f"/chart-profiles/{created['id']}").json() == created

    def test_defaults_give_an_empty_profile(self, client: TestClient) -> None:
        response = client.post("/chart-profiles", json={"name": "Пустой"})

        assert response.status_code == 201
        assert response.json()["config"]["indicators"] == []

    def test_rename_and_update_config(self, client: TestClient) -> None:
        profile = create(client)

        renamed = client.patch(
            f"/chart-profiles/{profile['id']}",
            json={
                "name": "Новое имя",
                "config": {"indicators": [], "layers": {"volume": False}},
            },
        ).json()

        assert renamed["name"] == "Новое имя"
        assert renamed["config"]["indicators"] == []
        assert renamed["updated_at"] >= profile["updated_at"]

    def test_delete(self, client: TestClient) -> None:
        profile = create(client)

        assert client.delete(f"/chart-profiles/{profile['id']}").status_code == 204
        assert client.get(f"/chart-profiles/{profile['id']}").status_code == 404
        assert client.delete(f"/chart-profiles/{profile['id']}").status_code == 404

    def test_unknown_profile_is_404(self, client: TestClient) -> None:
        assert client.get("/chart-profiles/999").status_code == 404
        assert (
            client.patch("/chart-profiles/999", json={"name": "x"}).status_code == 404
        )
        assert client.post("/chart-profiles/999/use").status_code == 404


class TestScopes:
    def test_names_are_unique_within_a_scope_only(
        self, client: TestClient, database_url: str
    ) -> None:
        ng, br = (
            make_instrument(database_url, "NG"),
            make_instrument(database_url, "BR"),
        )
        create(client, "Основной")

        same_global = client.post("/chart-profiles", json={"name": "Основной"})
        in_ng = client.post(
            "/chart-profiles", json={"name": "Основной", "instrument_id": ng}
        )
        in_ng_again = client.post(
            "/chart-profiles", json={"name": "Основной", "instrument_id": ng}
        )
        in_br = client.post(
            "/chart-profiles", json={"name": "Основной", "instrument_id": br}
        )

        assert same_global.status_code == 409
        assert in_ng.status_code == 201 and in_br.status_code == 201
        assert in_ng_again.status_code == 409

    def test_rename_to_an_existing_name_is_409(self, client: TestClient) -> None:
        create(client, "A")
        b = create(client, "B")

        response = client.patch(f"/chart-profiles/{b['id']}", json={"name": "A"})

        assert response.status_code == 409

    def test_listing_by_instrument_shows_global_and_own_profiles(
        self, client: TestClient, database_url: str
    ) -> None:
        ng, br = (
            make_instrument(database_url, "NG"),
            make_instrument(database_url, "BR"),
        )
        create(client, "Глобальный")
        create(client, "Только NG", instrument_id=ng)
        create(client, "Только BR", instrument_id=br)

        names = lambda root: sorted(  # noqa: E731
            p["name"]
            for p in client.get(
                "/chart-profiles", params={"instrument_id": root} if root else {}
            ).json()
        )

        assert names(ng) == ["Глобальный", "Только NG"]
        assert names(br) == ["Глобальный", "Только BR"]
        assert names(None) == ["Глобальный", "Только BR", "Только NG"]

    def test_unknown_instrument_is_404_and_deleting_it_drops_its_profiles(
        self, client: TestClient, database_url: str
    ) -> None:
        assert (
            client.post(
                "/chart-profiles", json={"name": "x", "instrument_id": 999}
            ).status_code
            == 404
        )
        instrument = make_instrument(database_url)
        create(client, "Свой", instrument_id=instrument)

        engine = make_engine(database_url)
        with Session(engine) as session:
            session.execute(delete(Instrument).where(Instrument.id == instrument))
            session.commit()
        engine.dispose()

        assert client.get("/chart-profiles").json() == []


class TestLastUsed:
    def test_use_marks_the_profile_and_puts_it_first(self, client: TestClient) -> None:
        a = create(client, "A")
        b = create(client, "B")
        assert [p["name"] for p in client.get("/chart-profiles").json()] == ["A", "B"]

        used = client.post(f"/chart-profiles/{b['id']}/use").json()

        assert used["last_used_at"] is not None
        listed = client.get("/chart-profiles").json()
        assert [p["name"] for p in listed] == ["B", "A"]
        assert a["last_used_at"] is None


class TestValidation:
    @pytest.mark.parametrize(
        ("indicators", "chart_tf", "message"),
        [
            ([{"name": "nope", "source_timeframe": "1h"}], "15m", "nope"),
            (
                [{"name": "sma", "params": {"period": 0}, "source_timeframe": "1h"}],
                "15m",
                "period",
            ),
            ([{"name": "sma", "source_timeframe": "15m"}], "1h", "запрещён"),
            ([{"name": "sma", "source_timeframe": "2h"}], None, "Неизвестный"),
        ],
    )
    def test_bad_indicators_are_rejected(
        self,
        client: TestClient,
        indicators: list[dict[str, Any]],
        chart_tf: str | None,
        message: str,
    ) -> None:
        response = client.post(
            "/chart-profiles",
            json={
                "name": "плохой",
                "config": {"chart_timeframe": chart_tf, "indicators": indicators},
            },
        )

        assert response.status_code == 422
        assert message in response.text

    def test_empty_name_is_rejected(self, client: TestClient) -> None:
        assert client.post("/chart-profiles", json={"name": ""}).status_code == 422
