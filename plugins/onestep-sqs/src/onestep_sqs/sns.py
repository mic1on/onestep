from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from onestep.envelope import Envelope
from onestep.resilience import ConnectorOperation, ConnectorOperationError

from onestep.connectors.base import Sink
from onestep.connectors.codec import encode_envelope

from .resilience import as_sqs_connector_operation_error, collect_sensitive_tokens

try:  # pragma: no cover - optional dependency
    import boto3
except ImportError:  # pragma: no cover - optional dependency
    boto3 = None


@dataclass
class SNSConnector:
    region_name: str | None = None
    options: dict[str, Any] | None = None
    client: Any | None = None
    _client: Any | None = field(default=None, init=False, repr=False)

    def _secret_tokens(self) -> list[str]:
        """Secret-bearing config tokens used to scrub error messages."""
        return collect_sensitive_tokens(self.options)

    def topic(
        self,
        arn: str,
        *,
        subject: str | None = None,
        message_group_id: str | None = None,
        deduplication_id_factory: Any | None = None,
        message_attributes: dict[str, Any] | None = None,
        retry_delay_s: float = 5.0,
    ) -> "SNSTopic":
        return SNSTopic(
            connector=self,
            arn=arn,
            subject=subject,
            message_group_id=message_group_id,
            deduplication_id_factory=deduplication_id_factory,
            message_attributes=message_attributes,
            retry_delay_s=retry_delay_s,
        )

    def get_client(self) -> Any:
        if self.client is not None:
            return self.client
        if self._client is None:
            if boto3 is None:
                raise RuntimeError("SNSConnector requires boto3. Install onestep-sqs.")
            self._client = boto3.client(
                "sns", region_name=self.region_name, **(self.options or {})
            )
        return self._client

    async def close(self) -> None:
        self._client = None


class SNSTopic(Sink):
    def __init__(
        self,
        *,
        connector: SNSConnector,
        arn: str,
        subject: str | None,
        message_group_id: str | None,
        deduplication_id_factory: Any | None,
        message_attributes: dict[str, Any] | None,
        retry_delay_s: float,
    ) -> None:
        Sink.__init__(self, arn)
        self.connector = connector
        self.arn = arn
        self.subject = subject
        self.message_group_id = message_group_id
        self.deduplication_id_factory = deduplication_id_factory
        self.message_attributes = message_attributes
        self.retry_delay_s = retry_delay_s
        self.client: Any | None = None

    async def open(self) -> None:
        try:
            if self.client is None:
                self.client = self.connector.get_client()
        except Exception as exc:
            connector_error = as_sqs_connector_operation_error(
                operation=ConnectorOperation.OPEN,
                exc=exc,
                source_name=self.name,
                retry_delay_s=self.retry_delay_s,
                secrets=self.connector._secret_tokens(),
                backend="sns",
            )
            if connector_error is None:
                raise
            raise connector_error from None

    async def send(self, envelope: Envelope) -> None:
        try:
            await self.open()
            params: dict[str, Any] = {
                "TopicArn": self.arn,
                "Message": encode_envelope(envelope).decode("utf-8"),
            }
            if self.subject is not None:
                params["Subject"] = self.subject
            if self.message_attributes:
                params["MessageAttributes"] = self.message_attributes
            if self.arn.endswith(".fifo"):
                group_id = self.message_group_id
                if not group_id:
                    raise ValueError("FIFO SNS topics require message_group_id")
                params["MessageGroupId"] = group_id
                if self.deduplication_id_factory is not None:
                    params["MessageDeduplicationId"] = self.deduplication_id_factory(
                        envelope
                    )
            await asyncio.to_thread(self.client.publish, **params)
        except ConnectorOperationError:
            raise
        except Exception as exc:
            connector_error = as_sqs_connector_operation_error(
                operation=ConnectorOperation.SEND,
                exc=exc,
                source_name=self.name,
                retry_delay_s=self.retry_delay_s,
                secrets=self.connector._secret_tokens(),
                backend="sns",
            )
            if connector_error is None:
                raise
            raise connector_error from None

    async def close(self) -> None:
        self.client = None
