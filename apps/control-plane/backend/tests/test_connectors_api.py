from __future__ import annotations

from uuid import UUID

from onestep_control_plane_api.api.connector_service import (
    ConnectorSecretError,
    _reset_cipher,
    build_connector_summary,
    build_runtime_connector_payload,
)
from onestep_control_plane_api.core.settings import settings
from onestep_control_plane_api.db.models import Connector
from sqlalchemy import select


def test_create_connector_encrypts_secret_and_masks_on_read(client, db_session) -> None:
    response = client.post(
        "/api/v1/connectors",
        json={
            "name": "prod-mysql",
            "type": "mysql",
            "config": {},
            "secret": {"dsn": "mysql://user:s3cret@10.0.0.1/db"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "prod-mysql"
    assert body["type"] == "mysql"
    # Secret is masked in the response.
    assert body["secret"] == {"dsn": "****", "password": "****"}

    # The DB row stores an encrypted token, not cleartext.
    row = db_session.scalar(select(Connector).where(Connector.name == "prod-mysql"))
    assert row is not None
    assert "s3cret" not in row.secret_encrypted
    assert row.secret_encrypted != ""
    summary = build_connector_summary(row, include_cleartext_secret=True)
    assert summary["secret"]["dsn"] == "mysql://user:s3cret@10.0.0.1/db"
    assert summary["secret"]["password"] == "s3cret"


def test_create_mysql_connector_from_split_fields_encodes_credentials(
    client, db_session
) -> None:
    response = client.post(
        "/api/v1/connectors",
        json={
            "name": "split-mysql",
            "type": "mysql",
            "config": {
                "host": "10.0.0.1",
                "port": "3306",
                "database": "orders",
                "username": "ops@example.com",
            },
            "secret": {"password": "pa@ss"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["config"]["username"] == "ops@example.com"
    assert body["secret"] == {"password": "****", "dsn": "****"}

    row = db_session.scalar(select(Connector).where(Connector.name == "split-mysql"))
    assert row is not None
    summary = build_connector_summary(row, include_cleartext_secret=True)
    assert summary["secret"]["dsn"] == (
        "mysql://ops%40example.com:pa%40ss@10.0.0.1:3306/orders"
    )

    runtime = build_runtime_connector_payload(row)
    assert runtime == {
        "type": "mysql",
        "config": {},
        "secret": {
            "dsn": "mysql://ops%40example.com:pa%40ss@10.0.0.1:3306/orders"
        },
    }


def test_update_mysql_connector_keeps_blank_password_and_rebuilds_dsn(
    client, db_session
) -> None:
    create = client.post(
        "/api/v1/connectors",
        json={
            "name": "split-edit",
            "type": "mysql",
            "config": {
                "host": "10.0.0.1",
                "port": "3306",
                "database": "orders",
                "username": "ops@example.com",
            },
            "secret": {"password": "pa@ss"},
        },
    )
    connector_id = create.json()["id"]

    response = client.put(
        f"/api/v1/connectors/{connector_id}",
        json={
            "config": {
                "host": "10.0.0.2",
                "port": "3306",
                "database": "billing",
                "username": "billing@example.com",
            },
            "secret": {"password": ""},
        },
    )
    assert response.status_code == 200

    db_session.expire_all()
    row = db_session.get(Connector, UUID(connector_id))
    summary = build_connector_summary(row, include_cleartext_secret=True)
    assert summary["secret"]["dsn"] == (
        "mysql://billing%40example.com:pa%40ss@10.0.0.2:3306/billing"
    )
    assert summary["secret"]["password"] == "pa@ss"


def test_list_connectors_filtered_by_type(client) -> None:
    client.post(
        "/api/v1/connectors",
        json={"name": "db1", "type": "mysql", "config": {}, "secret": {}},
    )
    client.post(
        "/api/v1/connectors",
        json={"name": "cache1", "type": "redis", "config": {}, "secret": {}},
    )

    response = client.get("/api/v1/connectors", params={"type": "mysql"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "db1"


def test_update_connector_keeps_secret_when_omitted(client, db_session) -> None:
    create = client.post(
        "/api/v1/connectors",
        json={
            "name": "mq1",
            "type": "rabbitmq",
            "config": {},
            "secret": {"url": "amqp://pw@host"},
        },
    )
    connector_id = create.json()["id"]

    # Update only the name, send no secret — the existing secret must persist.
    response = client.put(
        f"/api/v1/connectors/{connector_id}",
        json={"name": "mq1-renamed"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "mq1-renamed"

    row = db_session.get(Connector, UUID(connector_id))
    summary = build_connector_summary(row, include_cleartext_secret=True)
    assert summary["secret"] == {"url": "amqp://pw@host"}


def test_update_connector_secret_overwrites_value(client, db_session) -> None:
    create = client.post(
        "/api/v1/connectors",
        json={
            "name": "r1",
            "type": "redis",
            "config": {},
            "secret": {"url": "redis://old@host"},
        },
    )
    connector_id = create.json()["id"]

    response = client.put(
        f"/api/v1/connectors/{connector_id}",
        json={"secret": {"url": "redis://new@host"}},
    )
    assert response.status_code == 200
    assert response.json()["secret"] == {"url": "****"}

    row = db_session.get(Connector, UUID(connector_id))
    summary = build_connector_summary(row, include_cleartext_secret=True)
    assert summary["secret"] == {"url": "redis://new@host"}


def test_build_connector_summary_requires_connector_secret(client, db_session) -> None:
    create = client.post(
        "/api/v1/connectors",
        json={"name": "needs-secret", "type": "mysql", "config": {}, "secret": {}},
    )
    assert create.status_code == 200

    row = db_session.scalar(select(Connector).where(Connector.name == "needs-secret"))
    assert row is not None

    original_secret = settings.connector_secret
    settings.connector_secret = ""
    _reset_cipher()
    try:
        try:
            build_connector_summary(row)
        except ConnectorSecretError as exc:
            assert str(exc) == "ONESTEP_CP_CONNECTOR_SECRET is not configured"
        else:
            raise AssertionError("expected ConnectorSecretError")
    finally:
        settings.connector_secret = original_secret
        _reset_cipher()


def test_delete_connector(client) -> None:
    create = client.post(
        "/api/v1/connectors",
        json={"name": "to-delete", "type": "mysql", "config": {}, "secret": {}},
    )
    connector_id = create.json()["id"]

    response = client.delete(f"/api/v1/connectors/{connector_id}")
    assert response.status_code == 204

    gone = client.get(f"/api/v1/connectors/{connector_id}")
    assert gone.status_code == 404


def test_create_rejects_duplicate_name(client) -> None:
    client.post(
        "/api/v1/connectors",
        json={"name": "dup", "type": "mysql", "config": {}, "secret": {}},
    )
    response = client.post(
        "/api/v1/connectors",
        json={"name": "dup", "type": "mysql", "config": {}, "secret": {}},
    )
    assert response.status_code == 409


def test_create_rejects_unsupported_type(client) -> None:
    response = client.post(
        "/api/v1/connectors",
        json={"name": "bad", "type": "mongodb", "config": {}, "secret": {}},
    )
    assert response.status_code == 422
