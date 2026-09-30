"""OpenAPI-схема: валидна, каждый маршрут описан, документация доступна."""

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from trader_api.main import create_app

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def schema() -> dict[str, Any]:
    with TestClient(create_app(migrate_on_startup=False)) as client:
        return client.get("/openapi.json").json()


def operations() -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (path, method, operation)
        for path, item in schema()["paths"].items()
        for method, operation in item.items()
        if method in HTTP_METHODS
    ]


def test_every_operation_has_summary_tag_and_stable_id() -> None:
    found = operations()
    assert found
    ids = [op.get("operationId") for _, _, op in found]
    for path, method, op in found:
        assert op.get("summary"), (method, path)
        assert op.get("tags"), (method, path)
    assert all(ids) and len(set(ids)) == len(ids)
    assert "createJob" in ids and "getHealth" in ids


def test_error_responses_are_documented() -> None:
    paths = schema()["paths"]
    assert "404" in paths["/jobs/{job_id}"]["get"]["responses"]
    assert "409" in paths["/jobs/{job_id}/cancel"]["post"]["responses"]
    assert "503" in paths["/health"]["get"]["responses"]


def test_metadata_and_request_examples() -> None:
    document = schema()
    assert document["info"]["title"] == "Trader API"
    assert {tag["name"] for tag in document["tags"]} >= {"jobs", "system", "roots"}
    assert document["components"]["schemas"]["JobCreate"]["examples"]


def test_docs_pages_are_served() -> None:
    with TestClient(create_app(migrate_on_startup=False)) as client:
        assert client.get("/docs").status_code == 200
        assert client.get("/redoc").status_code == 200


def test_committed_client_schema_is_current() -> None:
    """`libs/api-client/openapi.json` совпадает со схемой приложения."""
    path = Path(__file__).resolve().parents[3] / "libs" / "api-client" / "openapi.json"
    expected = json.dumps(
        create_app(migrate_on_startup=False).openapi(), indent=2, ensure_ascii=False
    )

    assert path.read_text(encoding="utf-8").strip() == expected, (
        "Схема API изменилась: выполните `npx nx run api-client:generate`"
    )
