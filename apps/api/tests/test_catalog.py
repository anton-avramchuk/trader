"""REST каталога: root, календари, контракты (нужен TRADER_DATABASE_URL)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

ROOT = {
    "code": "NG",
    "name": "Природный газ",
    "quote_currency": "usd",
    "tick_size": "0.001",
}


def make_root(client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = client.post("/roots", json={**ROOT, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


class TestCalendars:
    def test_seeded_calendar_is_listed_with_rules(self, client: TestClient) -> None:
        listed = client.get("/calendars").json()
        detail = client.get("/calendars/moex_forts").json()

        assert [c["code"] for c in listed] == ["moex_forts"]
        assert detail["rules"] == 12 == len(detail["rule_list"])
        assert len(detail["fingerprint"]) == 64
        starts = [rule["effective_from"] for rule in detail["rule_list"]]
        assert starts == sorted(starts)

    def test_unknown_calendar_is_404(self, client: TestClient) -> None:
        assert client.get("/calendars/nope").status_code == 404


class TestRoots:
    def test_create_get_list_update(self, client: TestClient) -> None:
        created = make_root(client)

        assert created["quote_currency"] == "USD"
        assert created["calendar_code"] == "moex_forts"
        assert created["roll_trading_days"] == 5
        assert created["include_weekend_sessions"] is False
        assert client.get(f"/roots/{created['id']}").json() == created
        assert [r["code"] for r in client.get("/roots").json()] == ["NG"]

        updated = client.patch(
            f"/roots/{created['id']}",
            json={"roll_trading_days": 7, "include_weekend_sessions": True},
        ).json()
        assert updated["roll_trading_days"] == 7
        assert updated["include_weekend_sessions"] is True
        assert updated["code"] == "NG"

    def test_duplicate_code_is_409(self, client: TestClient) -> None:
        make_root(client)

        assert client.post("/roots", json=ROOT).status_code == 409

    @pytest.mark.parametrize(
        "bad",
        [
            {"tick_size": "0"},
            {"tick_size": "-1"},
            {"quote_currency": "US"},
            {"code": "has space"},
            {"calendar_code": "nope"},
            {"roll_trading_days": -1},
        ],
    )
    def test_invalid_values_are_422(
        self, client: TestClient, bad: dict[str, Any]
    ) -> None:
        assert client.post("/roots", json={**ROOT, **bad}).status_code == 422

    def test_unknown_root_and_delete(self, client: TestClient) -> None:
        assert client.get("/roots/999").status_code == 404
        assert client.patch("/roots/999", json={}).status_code == 404
        root = make_root(client)

        assert client.delete(f"/roots/{root['id']}").status_code == 204
        assert client.get(f"/roots/{root['id']}").status_code == 404

    def test_root_with_contracts_cannot_be_deleted(self, client: TestClient) -> None:
        root = make_root(client)
        client.post(
            f"/roots/{root['id']}/contracts", json={"expiration_date": "2026-12-29"}
        )

        assert client.delete(f"/roots/{root['id']}").status_code == 409


class TestContracts:
    def test_create_list_update_delete(self, client: TestClient) -> None:
        root = make_root(client)
        base = f"/roots/{root['id']}/contracts"
        second = client.post(base, json={"expiration_date": "2026-12-29"})
        first = client.post(
            base,
            json={
                "expiration_date": "2026-11-26",
                "last_trade_date": "2026-11-25",
                "secid": "NGX6",
            },
        )

        assert first.status_code == second.status_code == 201
        listed = client.get(base).json()
        assert [c["expiration_date"] for c in listed] == ["2026-11-26", "2026-12-29"]
        contract_id = first.json()["id"]
        patched = client.patch(f"/contracts/{contract_id}", json={"secid": "NGX26"})
        assert patched.json()["secid"] == "NGX26"
        assert client.get(f"/contracts/{contract_id}").json() == patched.json()
        assert client.delete(f"/contracts/{contract_id}").status_code == 204
        assert client.get(f"/contracts/{contract_id}").status_code == 404

    def test_duplicate_expiration_is_409_and_bad_dates_422(
        self, client: TestClient
    ) -> None:
        root = make_root(client)
        base = f"/roots/{root['id']}/contracts"
        client.post(base, json={"expiration_date": "2026-12-29"})

        duplicate = client.post(base, json={"expiration_date": "2026-12-29"})
        late = {"expiration_date": "2026-12-30", "last_trade_date": "2027-01-01"}
        contract_id = client.get(base).json()[0]["id"]
        patch = client.patch(
            f"/contracts/{contract_id}", json={"last_trade_date": "2027-01-01"}
        )

        assert duplicate.status_code == 409
        assert client.post(base, json=late).status_code == 422
        assert patch.status_code == 422

    def test_unknown_root_or_contract_is_404(self, client: TestClient) -> None:
        create = client.post(
            "/roots/999/contracts", json={"expiration_date": "2026-12-29"}
        )

        assert client.get("/roots/999/contracts").status_code == 404
        assert create.status_code == 404
        assert client.get("/contracts/999").status_code == 404
        assert client.delete("/contracts/999").status_code == 404


class TestIssContracts:
    def test_preview_enqueues_a_dry_run_sync(self, client: TestClient) -> None:
        root = make_root(client)

        job = client.post(
            f"/roots/{root['id']}/iss-preview", json={"from_year": 2020}
        ).json()

        assert job["type"] == "iss.sync_root"
        assert job["params"] == {
            "root_id": root["id"],
            "dry_run": True,
            "from_year": 2020,
        }
        assert client.post("/roots/999/iss-preview", json={}).status_code == 404

    def test_confirmed_contracts_are_created_with_iss_ids_and_imports_queued(
        self, client: TestClient
    ) -> None:
        root = make_root(client)
        url = f"/roots/{root['id']}/contracts/from-iss"
        body = {
            "contracts": [
                {"secid": "NGX0", "expiration_date": "2020-11-30"},
                {
                    "secid": "NGZ0",
                    "expiration_date": "2020-12-30",
                    "last_trade_date": "2020-12-29",
                },
            ]
        }

        first = client.post(url, json=body)
        again = client.post(url, json=body)

        assert first.status_code == 201, first.text
        result = first.json()
        assert result["created"] == 2 and len(result["imports_enqueued"]) == 2
        assert result["contracts"][0]["provider_ids"] == [
            {"provider": "moex_iss", "id_type": "secid", "external_id": "NGX0"}
        ]
        # Повтор: контракты те же, а загрузки уже ждут в очереди.
        assert again.json()["created"] == 0
        assert again.json()["imports_enqueued"] == []
        jobs = client.get("/jobs", params={"type": "import.iss"}).json()
        assert len(jobs) == 2

    def test_without_enqueue_no_jobs_are_created(self, client: TestClient) -> None:
        root = make_root(client)
        body = {
            "contracts": [{"secid": "NGX0", "expiration_date": "2020-11-30"}],
            "enqueue_imports": False,
        }

        response = client.post(f"/roots/{root['id']}/contracts/from-iss", json=body)

        assert response.json()["imports_enqueued"] == []
        assert client.get("/jobs").json() == []
