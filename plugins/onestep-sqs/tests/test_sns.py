from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from onestep import Envelope
from onestep.config import load_app_config
from onestep.resilience import ConnectorOperationError
from onestep.resource_registry import ResourceRegistry

from onestep_sqs import SNSConnector, SNSTopic, register


class FakeSNSClient:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    def publish(self, **kwargs: Any) -> dict[str, Any]:
        self.published.append(kwargs)
        return {"MessageId": f"msg-{len(self.published)}"}


def test_sns_plugin_registers_catalog_metadata() -> None:
    registry = ResourceRegistry()
    register(registry)
    catalog = {entry.type: entry for entry in registry.catalog_entries()}
    connector_fields = {field.name: field for field in catalog["sns"].fields}
    topic_fields = {field.name: field for field in catalog["sns_topic"].fields}
    assert catalog["sns"].roles == ("connector",)
    assert connector_fields["options"].secret is True
    assert catalog["sns_topic"].roles == ("sink",)
    assert catalog["sns_topic"].connector_types == ("sns",)
    assert topic_fields["arn"].required is True
    assert topic_fields["arn"].secret is True


def test_yaml_builds_sns_resources_via_plugin_entry_point() -> None:
    app = load_app_config(
        {
            "apiVersion": "onestep/v1alpha1",
            "kind": "App",
            "app": {"name": "sns-plugin"},
            "resources": {
                "sns": {
                    "type": "sns",
                    "region_name": "ap-southeast-1",
                    "options": {"endpoint_url": "http://localstack:4566"},
                },
                "notify": {
                    "type": "sns_topic",
                    "connector": "sns",
                    "arn": "arn:aws:sns:ap-southeast-1:123456789012:notify",
                    "subject": "hello",
                },
            },
            "tasks": [],
        },
        strict=True,
    )
    assert isinstance(app.resources["sns"], SNSConnector)
    assert app.resources["sns"].region_name == "ap-southeast-1"
    assert app.resources["sns"].options == {"endpoint_url": "http://localstack:4566"}
    assert isinstance(app.resources["notify"], SNSTopic)
    assert app.resources["notify"].connector is app.resources["sns"]
    assert app.resources["notify"].arn.endswith(":notify")
    assert app.resources["notify"].subject == "hello"


def test_sns_topic_publishes_encoded_envelope() -> None:
    client = FakeSNSClient()
    connector = SNSConnector(client=client)
    topic = connector.topic(
        "arn:aws:sns:us-east-1:123456789012:events",
        subject="event",
        message_attributes={"kind": {"DataType": "String", "StringValue": "job"}},
    )
    asyncio.run(topic.send(Envelope(body={"job": 1})))
    assert len(client.published) == 1
    call = client.published[0]
    assert call["TopicArn"] == "arn:aws:sns:us-east-1:123456789012:events"
    assert call["Subject"] == "event"
    assert call["MessageAttributes"] == {
        "kind": {"DataType": "String", "StringValue": "job"}
    }
    assert json.loads(call["Message"])["body"] == {"job": 1}


def test_sns_topic_fifo_requires_message_group_id() -> None:
    client = FakeSNSClient()
    connector = SNSConnector(client=client)
    topic = connector.topic("arn:aws:sns:us-east-1:123456789012:events.fifo")
    with pytest.raises(ValueError, match="message_group_id"):
        asyncio.run(topic.send(Envelope(body={"job": 1})))


def test_sns_topic_fifo_sets_group_and_dedup() -> None:
    client = FakeSNSClient()
    connector = SNSConnector(client=client)
    topic = connector.topic(
        "arn:aws:sns:us-east-1:123456789012:events.fifo",
        message_group_id="jobs",
        deduplication_id_factory=lambda env: str(env.body["job"]),
    )
    asyncio.run(topic.send(Envelope(body={"job": 7})))
    call = client.published[0]
    assert call["MessageGroupId"] == "jobs"
    assert call["MessageDeduplicationId"] == "7"


def test_sns_connector_error_does_not_leak_option_secrets() -> None:
    secret_key = "AKIAIOSFODNN7EXAMPLE"
    secret_token = "IQoJb3JpZ2luX2VjEPn//////////wEaCXVzLXdlc3QtMiJIMEYCIQ"

    class BrokenClient:
        def publish(self, **kwargs: Any) -> None:
            raise ConnectionError(
                f"Access denied with key {secret_key} and token {secret_token}"
            )

    connector = SNSConnector(
        options={
            "aws_access_key_id": secret_key,
            "aws_session_token": secret_token,
        },
        client=BrokenClient(),
    )
    topic = connector.topic("arn:aws:sns:us-east-1:123456789012:events")
    with pytest.raises(ConnectorOperationError) as captured:
        asyncio.run(topic.send(Envelope(body={"job": 1})))
    message = str(captured.value.cause)
    assert captured.value.backend == "sns"
    assert secret_key not in message
    assert secret_token not in message
