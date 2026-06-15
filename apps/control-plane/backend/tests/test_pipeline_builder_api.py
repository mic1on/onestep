from __future__ import annotations

from uuid import UUID


def minimal_graph_payload() -> dict[str, object]:
    return {
        "nodes": [
            {
                "id": "source",
                "type": "interval_source",
                "kind": "source",
                "config": {"seconds": 60},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "handler",
                "type": "handler",
                "kind": "handler",
                "mode": "visual",
                "mapping": {"ok": "{{ payload.ok }}"},
                "position": {"x": 240, "y": 0},
            },
            {
                "id": "sink",
                "type": "http_sink",
                "kind": "sink",
                "config": {"url": "https://example.invalid/hook"},
                "position": {"x": 480, "y": 0},
            },
        ],
        "edges": [
            {"from": "source", "to": "handler"},
            {"from": "handler", "to": "sink"},
        ],
    }


def test_pipeline_crud_and_validate(client) -> None:
    create_response = client.post(
        "/api/v1/pipelines",
        json={
            "name": "Daily Sync",
            "description": "Build from UI",
            "graph": minimal_graph_payload(),
        },
    )
    assert create_response.status_code == 200
    created = create_response.json()
    UUID(created["id"])
    assert created["status"] == "draft"
    assert created["graph"]["nodes"][0]["id"] == "source"

    list_response = client.get("/api/v1/pipelines")
    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["name"] == "Daily Sync"

    validate_response = client.post(f"/api/v1/pipelines/{created['id']}/validate")
    assert validate_response.status_code == 200
    assert validate_response.json() == {"ok": True, "message": "pipeline is valid"}

    get_response = client.get(f"/api/v1/pipelines/{created['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "valid"

    update_response = client.put(
        f"/api/v1/pipelines/{created['id']}",
        json={"name": "Daily Sync v2"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["name"] == "Daily Sync v2"
    assert update_response.json()["status"] == "draft"

    delete_response = client.delete(f"/api/v1/pipelines/{created['id']}")
    assert delete_response.status_code == 204
    assert client.get(f"/api/v1/pipelines/{created['id']}").status_code == 404


def test_validate_invalid_pipeline_updates_status(client) -> None:
    create_response = client.post(
        "/api/v1/pipelines",
        json={"name": "Broken", "description": "", "graph": {"nodes": [], "edges": []}},
    )
    assert create_response.status_code == 200
    pipeline_id = create_response.json()["id"]

    validate_response = client.post(f"/api/v1/pipelines/{pipeline_id}/validate")
    assert validate_response.status_code == 422
    assert "at least one node" in validate_response.json()["detail"]

    get_response = client.get(f"/api/v1/pipelines/{pipeline_id}")
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "invalid"
