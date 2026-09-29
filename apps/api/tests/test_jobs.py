"""Интеграционные тесты REST/WebSocket очереди задач (нужен TRADER_DATABASE_URL)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from starlette.websockets import WebSocketDisconnect
from trader_db import (
    claim_next_job,
    complete_job,
    make_engine,
    touch_job,
)


def create(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/jobs", json={"type": "demo.sleep", **body})
    assert response.status_code == 201, response.text
    return response.json()


class TestRest:
    def test_create_and_get_job(self, client: TestClient) -> None:
        created = create(client, params={"seconds": 1})

        assert created["status"] == "queued"
        assert created["params"] == {"seconds": 1}
        assert created["progress"] == 0
        assert client.get(f"/jobs/{created['id']}").json() == created

    def test_unknown_job_is_404(self, client: TestClient) -> None:
        assert client.get("/jobs/999999").status_code == 404
        assert client.post("/jobs/999999/cancel").status_code == 404

    @pytest.mark.parametrize("job_type", ["", "Has Spaces", "UPPER", "x" * 65])
    def test_invalid_type_is_rejected(self, client: TestClient, job_type: str) -> None:
        response = client.post("/jobs", json={"type": job_type})

        assert response.status_code == 422

    def test_max_attempts_is_validated(self, client: TestClient) -> None:
        response = client.post("/jobs", json={"type": "a", "max_attempts": 0})

        assert response.status_code == 422

    def test_list_is_newest_first_with_filters(self, client: TestClient) -> None:
        first = create(client)
        second = create(client, type="reconcile")

        everything = client.get("/jobs").json()
        only_reconcile = client.get("/jobs", params={"type": "reconcile"}).json()
        only_running = client.get("/jobs", params={"status": "running"}).json()
        limited = client.get("/jobs", params={"limit": 1}).json()

        assert [j["id"] for j in everything] == [second["id"], first["id"]]
        assert [j["id"] for j in only_reconcile] == [second["id"]]
        assert only_running == []
        assert [j["id"] for j in limited] == [second["id"]]

    def test_cancel_queued_job(self, client: TestClient) -> None:
        job = create(client)

        response = client.post(f"/jobs/{job['id']}/cancel")

        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"

    def test_cancel_running_job_sets_flag(
        self, client: TestClient, database_url: str
    ) -> None:
        job = create(client)
        engine = make_engine(database_url)
        with sessionmaker(engine)() as session:
            claim_next_job(session, "w1")
            session.commit()
        engine.dispose()

        response = client.post(f"/jobs/{job['id']}/cancel")

        assert response.status_code == 200
        assert (response.json()["status"], response.json()["cancel_requested"]) == (
            "running",
            True,
        )

    def test_cancel_finished_job_is_409(
        self, client: TestClient, database_url: str
    ) -> None:
        job = create(client)
        engine = make_engine(database_url)
        with sessionmaker(engine)() as session:
            claim_next_job(session, "w1")
            complete_job(session, job["id"], "w1")
            session.commit()
        engine.dispose()

        assert client.post(f"/jobs/{job['id']}/cancel").status_code == 409


class TestWebSocket:
    def test_progress_is_pushed_until_the_job_finishes(
        self, client: TestClient, database_url: str
    ) -> None:
        job_id = create(client)["id"]
        engine = make_engine(database_url)
        factory = sessionmaker(engine)

        with client.websocket_connect(f"/ws/jobs/{job_id}") as socket:
            assert socket.receive_json()["status"] == "queued"

            with factory() as session:
                claim_next_job(session, "w1")
                session.commit()
            assert socket.receive_json()["status"] == "running"

            with factory() as session:
                touch_job(session, job_id, "w1", progress=0.5, message="половина")
                session.commit()
            half = socket.receive_json()
            assert (half["progress"], half["progress_message"]) == (0.5, "половина")

            with factory() as session:
                complete_job(session, job_id, "w1", {"ok": True})
                session.commit()
            done = socket.receive_json()
            assert (done["status"], done["progress"], done["result"]) == (
                "succeeded",
                1.0,
                {"ok": True},
            )

            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_json()
            assert closed.value.code == 1000
        engine.dispose()

    def test_finished_job_is_sent_once_and_closed(
        self, client: TestClient, database_url: str
    ) -> None:
        job_id = create(client)["id"]
        client.post(f"/jobs/{job_id}/cancel")

        with client.websocket_connect(f"/ws/jobs/{job_id}") as socket:
            assert socket.receive_json()["status"] == "cancelled"
            with pytest.raises(WebSocketDisconnect):
                socket.receive_json()

    def test_unknown_job_closes_with_4404(self, client: TestClient) -> None:
        with (
            client.websocket_connect("/ws/jobs/999999") as socket,
            pytest.raises(WebSocketDisconnect) as closed,
        ):
            socket.receive_json()

        assert closed.value.code == 4404
