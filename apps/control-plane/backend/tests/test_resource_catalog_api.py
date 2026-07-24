from __future__ import annotations


def test_resource_catalog_api_returns_installed_onestep_catalog(client) -> None:
    response = client.get("/api/v1/resource-catalog")

    assert response.status_code == 200
    resources = {item["type"]: item for item in response.json()["resources"]}
    assert resources["memory"]["roles"] == ["source", "sink"]
    assert resources["http_sink"]["roles"] == ["sink"]
    assert resources["http_sink"]["topology_fields"] == [
        "url",
        "method",
        "timeout_s",
        "success_statuses",
    ]
    method = next(field for field in resources["http_sink"]["fields"] if field["name"] == "method")
    assert method["default"] == "POST"


def test_resource_catalog_api_includes_connector_secret_metadata(client) -> None:
    response = client.get("/api/v1/resource-catalog")

    assert response.status_code == 200
    resources = {item["type"]: item for item in response.json()["resources"]}
    mysql_fields = {field["name"]: field for field in resources["mysql"]["fields"]}
    assert resources["mysql"]["roles"] == ["connector"]
    assert mysql_fields["dsn"]["required"] is True
    assert mysql_fields["dsn"]["secret"] is True
    assert mysql_fields["password"]["secret"] is True
