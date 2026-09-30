"""REST импорта: запуск, файлы, пресеты, отчёты, конфликты (нужен БД)."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from trader_db import (
    extend_dataset_version,
    finish_import,
    insert_candles,
    make_engine,
    start_import,
)
from trader_engine.ingest import Candle1m

T0 = datetime(2026, 9, 28, 7, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def import_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "imports"
    monkeypatch.setenv("TRADER_IMPORT_DIR", str(directory))
    return directory


@pytest.fixture
def contract_id(client: TestClient) -> int:
    root = client.post(
        "/roots",
        json={
            "code": "NG",
            "name": "Gas",
            "quote_currency": "USD",
            "tick_size": "0.001",
        },
    ).json()
    contract = client.post(
        f"/roots/{root['id']}/contracts", json={"expiration_date": "2026-12-29"}
    ).json()
    return contract["id"]


def candle(close: str, minute: int = 0) -> Candle1m:
    value = Decimal(close)
    return Candle1m(
        timestamp=T0.replace(minute=minute),
        open=value,
        high=value + 1,
        low=value - 1,
        close=value,
        volume=Decimal(5),
    )


def load(database_url: str, contract_id: int, candles: list[Candle1m]) -> int:
    engine = make_engine(database_url)
    with Session(engine) as session:
        import_id = start_import(
            session, provider_code="csv", contract_id=contract_id, source_type="file"
        )
        insert_candles(session, import_id, candles)
        finish_import(session, import_id)
        extend_dataset_version(session, import_id)
        session.commit()
    engine.dispose()
    return import_id


class TestStartImports:
    def test_iss_import_job_carries_the_window(
        self, client: TestClient, contract_id: int
    ) -> None:
        response = client.post(
            f"/contracts/{contract_id}/imports/iss",
            json={"from": "2020-11-01", "till": "2020-11-30"},
        )

        assert response.status_code == 201
        assert response.json()["type"] == "import.iss"
        assert response.json()["params"] == {
            "contract_id": contract_id,
            "from": "2020-11-01",
            "till": "2020-11-30",
        }

    def test_iss_import_validation(self, client: TestClient, contract_id: int) -> None:
        backwards = {"from": "2020-12-01", "till": "2020-11-01"}

        rejected = client.post(f"/contracts/{contract_id}/imports/iss", json=backwards)

        assert rejected.status_code == 422
        assert client.post("/contracts/999/imports/iss", json={}).status_code == 404

    def test_file_import_needs_an_uploaded_file_and_one_mapping(
        self, client: TestClient, contract_id: int
    ) -> None:
        url = f"/contracts/{contract_id}/imports/file"

        missing = client.post(url, json={"file": "a.csv", "preset": "finam"})
        client.put("/import-files/a.csv", content=b"x")
        none = client.post(url, json={"file": "a.csv"})
        both = client.post(
            url, json={"file": "a.csv", "preset": "finam", "mapping": {}}
        )
        job = client.post(url, json={"file": "a.csv", "preset": "finam"})

        assert missing.status_code == 404
        assert none.status_code == both.status_code == 422
        assert job.status_code == 201
        assert job.json()["params"]["preset"] == "finam"
        assert job.json()["type"] == "import.file"


class TestFiles:
    def test_upload_list_replace_delete(
        self, client: TestClient, import_dir: Path
    ) -> None:
        first = client.put("/import-files/NG 2020.csv", content=b"a,b\n1,2\n")
        client.put("/import-files/NG 2020.csv", content=b"longer content")

        assert first.status_code == 200 and first.json()["size"] == 8
        listed = client.get("/import-files").json()
        assert [(f["name"], f["size"]) for f in listed] == [("NG 2020.csv", 14)]
        assert not list(import_dir.glob("*.part"))
        assert client.delete("/import-files/NG 2020.csv").status_code == 204
        assert client.delete("/import-files/NG 2020.csv").status_code == 404
        assert client.get("/import-files").json() == []

    @pytest.mark.parametrize("name", [".hidden", "-x.csv", "a%2F..%2Fc.csv"])
    def test_dangerous_names_are_rejected(self, client: TestClient, name: str) -> None:
        response = client.put(f"/import-files/{name}", content=b"x")

        assert response.status_code in (404, 422)

    def test_size_limit(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        import_dir: Path,
    ) -> None:
        monkeypatch.setenv("TRADER_MAX_UPLOAD_BYTES", "10")

        response = client.put("/import-files/big.csv", content=b"x" * 11)

        assert response.status_code == 413
        assert not list(import_dir.glob("big.csv*"))


class TestPresets:
    def test_builtin_presets_and_user_presets(self, client: TestClient) -> None:
        listed = client.get("/import-presets").json()
        builtin = next(p for p in listed if p["name"] == "finam")
        assert builtin["builtin"] is True

        saved = client.put(
            "/import-presets/mine",
            json={"description": "мой", "mapping": builtin["mapping"]},
        )
        overwrite = client.put(
            "/import-presets/finam", json={"mapping": builtin["mapping"]}
        )
        invalid = client.put("/import-presets/mine", json={"mapping": {"open": "x"}})

        assert saved.status_code == 200 and saved.json()["builtin"] is False
        assert overwrite.status_code == 409
        assert invalid.status_code == 422
        assert client.delete("/import-presets/mine").status_code == 204
        assert client.delete("/import-presets/mine").status_code == 404


class TestReportsAndConflicts:
    def test_import_report_is_readable(
        self, client: TestClient, database_url: str, contract_id: int
    ) -> None:
        import_id = load(database_url, contract_id, [candle("100")])

        got = client.get(f"/imports/{import_id}").json()
        listed = client.get("/imports", params={"contract_id": contract_id}).json()

        assert got["status"] == "completed" and got["provider"] == "csv"
        assert [i["id"] for i in listed] == [import_id]
        assert client.get("/imports/999").status_code == 404
        assert client.get("/imports/999/errors").status_code == 404
        assert client.get(f"/imports/{import_id}/errors").json() == []

    def test_conflicts_are_listed_and_accepting_creates_version_and_job(
        self, client: TestClient, database_url: str, contract_id: int
    ) -> None:
        load(database_url, contract_id, [candle("100"), candle("101", 1)])
        second = load(database_url, contract_id, [candle("105"), candle("101", 1)])

        conflicts = client.get(f"/imports/{second}/conflicts").json()

        assert len(conflicts) == 1
        assert conflicts[0]["existing"]["close"] == "100.00000000"
        assert conflicts[0]["incoming"]["close"] == "105.00000000"
        resolved = client.post(
            f"/imports/{second}/conflicts/resolve", json={"accept": True}
        ).json()
        assert resolved["resolved"] == 1
        assert resolved["dataset_version_id"] is not None
        assert resolved["aggregation_job_id"] is not None
        assert client.get(f"/imports/{second}/conflicts").json() == []
        accepted = client.get(
            f"/imports/{second}/conflicts", params={"status": "accepted"}
        ).json()
        assert accepted[0]["resolution_import_id"] == resolved["resolution_import_id"]
        jobs = client.get("/jobs", params={"type": "aggregate.contract"}).json()
        assert [j["params"] for j in jobs] == [{"contract_id": contract_id}]

    def test_rejecting_changes_nothing(
        self, client: TestClient, database_url: str, contract_id: int
    ) -> None:
        load(database_url, contract_id, [candle("100")])
        second = load(database_url, contract_id, [candle("105")])

        resolved: dict[str, Any] = client.post(
            f"/imports/{second}/conflicts/resolve", json={"accept": False}
        ).json()

        assert resolved == {
            "resolved": 1,
            "resolution_import_id": None,
            "dataset_version_id": None,
            "aggregation_job_id": None,
        }
        rejected = client.get(
            f"/imports/{second}/conflicts", params={"status": "rejected"}
        ).json()
        assert [c["status"] for c in rejected] == ["rejected"]
        missing = client.post("/imports/999/conflicts/resolve", json={"accept": True})
        assert missing.status_code == 404

    def test_nothing_pending_resolves_zero(
        self, client: TestClient, database_url: str, contract_id: int
    ) -> None:
        import_id = load(database_url, contract_id, [candle("100")])

        resolved = client.post(
            f"/imports/{import_id}/conflicts/resolve", json={"accept": True}
        ).json()

        assert resolved["resolved"] == 0 and resolved["resolution_import_id"] is None
