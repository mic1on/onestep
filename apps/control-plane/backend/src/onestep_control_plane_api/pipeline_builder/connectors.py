from __future__ import annotations

from onestep_control_plane_api.pipeline_builder.schemas import ConnectorDescriptor, ConnectorField

CONNECTORS: list[ConnectorDescriptor] = [
    ConnectorDescriptor(
        type="mysql_source",
        label="MySQL Source",
        category="source",
        description="Read from MySQL table_queue, incremental rows, or binlog.",
        credential_type="mysql",
        fields=[
            ConnectorField(name="mode", label="Mode", required=True),
            ConnectorField(name="table", label="Table", required=True),
            ConnectorField(name="cursor_column", label="Cursor Column"),
        ],
    ),
    ConnectorDescriptor(
        type="postgres_source",
        label="Postgres Source",
        category="source",
        description="Read from PostgreSQL incremental rows or table_queue.",
        credential_type="postgres",
        fields=[
            ConnectorField(name="mode", label="Mode", required=True),
            ConnectorField(name="table", label="Table", required=True),
            ConnectorField(name="key", label="Key Column"),
            ConnectorField(name="cursor_column", label="Cursor Column"),
            ConnectorField(name="where", label="Where"),
            ConnectorField(name="claim", label="Claim JSON"),
            ConnectorField(name="ack", label="Ack JSON"),
            ConnectorField(name="nack", label="Nack JSON"),
            ConnectorField(name="batch_size", label="Batch Size", type="number"),
            ConnectorField(name="poll_interval_s", label="Poll Interval", type="number"),
        ],
    ),
    ConnectorDescriptor(
        type="rabbitmq_source",
        label="RabbitMQ Source",
        category="source",
        description="Consume messages from a RabbitMQ queue.",
        credential_type="rabbitmq",
        fields=[
            ConnectorField(name="queue", label="Queue", required=True),
            ConnectorField(name="exchange", label="Exchange"),
            ConnectorField(name="routing_key", label="Routing"),
            ConnectorField(name="prefetch", label="Prefetch", type="number"),
            ConnectorField(name="sample_payload", label="Sample Payload JSON"),
        ],
    ),
    ConnectorDescriptor(
        type="redis_stream_source",
        label="Redis Stream Source",
        category="source",
        description="Consume Redis Stream entries.",
        credential_type="redis",
        fields=[
            ConnectorField(name="stream", label="Stream", required=True),
            ConnectorField(name="group", label="Group"),
            ConnectorField(name="consumer", label="Consumer"),
            ConnectorField(name="batch_size", label="Batch Size", type="number"),
        ],
    ),
    ConnectorDescriptor(
        type="sqs_source",
        label="SQS Source",
        category="source",
        description="Consume AWS SQS messages.",
        credential_type="sqs",
        fields=[
            ConnectorField(name="url", label="Queue URL", required=True),
            ConnectorField(name="wait_time_s", label="Wait Time", type="number"),
            ConnectorField(name="visibility_timeout", label="Visibility Timeout", type="number"),
            ConnectorField(name="sample_payload", label="Sample Payload JSON"),
        ],
    ),
    ConnectorDescriptor(
        type="cron_source",
        label="Cron Source",
        category="source",
        description="Trigger from cron expressions.",
        fields=[
            ConnectorField(name="expression", label="Expression", required=True),
            ConnectorField(name="timezone", label="Timezone"),
            ConnectorField(name="payload", label="Payload JSON"),
        ],
    ),
    ConnectorDescriptor(
        type="interval_source",
        label="Interval Source",
        category="source",
        description="Trigger on fixed intervals.",
        fields=[
            ConnectorField(name="seconds", label="Seconds", type="number", required=True),
            ConnectorField(name="payload", label="Payload JSON"),
        ],
    ),
    ConnectorDescriptor(
        type="webhook_source",
        label="Webhook Source",
        category="source",
        description="Expose an HTTP entrypoint.",
        fields=[
            ConnectorField(name="path", label="Path", required=True),
            ConnectorField(name="methods", label="Methods"),
            ConnectorField(name="sample_payload", label="Sample Payload JSON"),
        ],
    ),
    ConnectorDescriptor(
        type="feishu_bitable_source",
        label="Feishu Bitable Source",
        category="source",
        description="Read incremental Feishu Bitable records.",
        credential_type="feishu_bitable",
        fields=[
            ConnectorField(name="app_token", label="App Token", required=True),
            ConnectorField(name="table_id", label="Table ID", required=True),
            ConnectorField(name="cursor_field", label="Cursor Field", required=True),
            ConnectorField(name="batch_size", label="Batch Size", type="number"),
        ],
    ),
    ConnectorDescriptor(type="handler", label="Python Handler", category="handler", description="Transform payloads with visual mappings or Python code."),
    ConnectorDescriptor(
        type="mysql_sink",
        label="MySQL Sink",
        category="sink",
        description="Write rows to a MySQL table.",
        credential_type="mysql",
        fields=[
            ConnectorField(name="table", label="Table", required=True),
            ConnectorField(name="mode", label="Mode"),
            ConnectorField(name="keys", label="Keys CSV"),
        ],
    ),
    ConnectorDescriptor(
        type="postgres_sink",
        label="Postgres Sink",
        category="sink",
        description="Write rows to a PostgreSQL table.",
        credential_type="postgres",
        fields=[
            ConnectorField(name="table", label="Table", required=True),
            ConnectorField(name="mode", label="Mode"),
            ConnectorField(name="keys", label="Keys CSV"),
        ],
    ),
    ConnectorDescriptor(
        type="rabbitmq_sink",
        label="RabbitMQ Sink",
        category="sink",
        description="Publish messages to RabbitMQ.",
        credential_type="rabbitmq",
        fields=[
            ConnectorField(name="queue", label="Queue", required=True),
            ConnectorField(name="exchange", label="Exchange"),
            ConnectorField(name="routing_key", label="Routing"),
        ],
    ),
    ConnectorDescriptor(
        type="redis_stream_sink",
        label="Redis Stream Sink",
        category="sink",
        description="Append entries to a Redis Stream.",
        credential_type="redis",
        fields=[
            ConnectorField(name="stream", label="Stream", required=True),
            ConnectorField(name="maxlen", label="Max Length", type="number"),
        ],
    ),
    ConnectorDescriptor(
        type="sqs_sink",
        label="SQS Sink",
        category="sink",
        description="Publish messages to AWS SQS.",
        credential_type="sqs",
        fields=[
            ConnectorField(name="url", label="Queue URL", required=True),
            ConnectorField(name="message_group_id", label="Message Group ID"),
        ],
    ),
    ConnectorDescriptor(
        type="http_sink",
        label="HTTP Sink",
        category="sink",
        description="Call an external HTTP endpoint.",
        fields=[
            ConnectorField(name="url", label="URL", required=True),
            ConnectorField(name="method", label="Method"),
            ConnectorField(name="timeout_s", label="Timeout", type="number"),
        ],
    ),
    ConnectorDescriptor(
        type="feishu_bitable_sink",
        label="Feishu Bitable Sink",
        category="sink",
        description="Write records to Feishu Bitable.",
        credential_type="feishu_bitable",
        fields=[
            ConnectorField(name="app_token", label="App Token", required=True),
            ConnectorField(name="table_id", label="Table ID", required=True),
            ConnectorField(name="mode", label="Mode"),
            ConnectorField(name="match_fields", label="Match Fields CSV"),
        ],
    ),
]


CONNECTOR_BY_TYPE = {connector.type: connector for connector in CONNECTORS}
