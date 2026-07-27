from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

from onestep import (
    ResourceBuildContext,
    ResourceCatalogEntry,
    ResourceCatalogField,
    ResourceRegistry,
    ResourceSpecHandler,
    ResourceValidationContext,
)

from .connector import ElasticsearchConnector

CONNECTOR_FIELDS = frozenset(
    {
        "type",
        "hosts",
        "distribution",
        "username",
        "password",
        "api_key",
        "bearer_token",
        "headers",
        "verify_certs",
        "ca_certs",
        "client_cert",
        "client_key",
        "request_timeout_s",
    }
)
SINK_FIELDS = frozenset(
    {
        "type",
        "connector",
        "index",
        "operation",
        "id_field",
        "chunk_size",
        "max_chunk_bytes",
        "refresh",
        "pipeline",
    }
)

CONNECTOR_CATALOG = ResourceCatalogEntry(
    type="elasticsearch",
    roles=("connector",),
    label="Elasticsearch / OpenSearch",
    fields=(
        ResourceCatalogField("hosts", "string_list", required=True),
        ResourceCatalogField(
            "distribution",
            "string",
            default="auto",
            options=("auto", "elasticsearch", "opensearch"),
        ),
        ResourceCatalogField("username", "string"),
        ResourceCatalogField("password", "string", secret=True),
        ResourceCatalogField("api_key", "string", secret=True),
        ResourceCatalogField("bearer_token", "string", secret=True),
        ResourceCatalogField("headers", "mapping", secret=True),
        ResourceCatalogField("verify_certs", "boolean", default=True),
        ResourceCatalogField("ca_certs", "string"),
        ResourceCatalogField("client_cert", "string"),
        ResourceCatalogField("client_key", "string", secret=True),
        ResourceCatalogField("request_timeout_s", "number", default=10.0),
    ),
)
SINK_CATALOG = ResourceCatalogEntry(
    type="elasticsearch_bulk_sink",
    roles=("sink",),
    label="Elasticsearch Bulk Sink",
    connector_types=("elasticsearch",),
    fields=(
        ResourceCatalogField("connector", "ref", required=True),
        ResourceCatalogField("index", "string", required=True),
        ResourceCatalogField(
            "operation", "string", default="index", options=("index", "create")
        ),
        ResourceCatalogField("id_field", "string"),
        ResourceCatalogField("chunk_size", "integer", default=500),
        ResourceCatalogField("max_chunk_bytes", "integer", default=5_000_000),
        ResourceCatalogField("refresh", "json", default=False),
        ResourceCatalogField("pipeline", "string"),
    ),
    topology_fields=("index", "operation", "chunk_size"),
)


def _hosts(value: Any, *, field: str) -> list[str]:
    values = (
        [value]
        if isinstance(value, str)
        else list(value)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes))
        else []
    )
    if not values or any(
        not isinstance(item, str)
        or urlparse(item).scheme not in {"http", "https"}
        or not urlparse(item).netloc
        for item in values
    ):
        raise ValueError(f"'{field}' must contain non-empty HTTP(S) URLs")
    return values


def _validate_connector(
    ctx: ResourceValidationContext, spec: Mapping[str, Any]
) -> None:
    _hosts(spec.get("hosts"), field=f"{ctx.field}.hosts")
    distribution = spec.get("distribution", "auto")
    if distribution not in {"auto", "elasticsearch", "opensearch"}:
        raise ValueError(f"'{ctx.field}.distribution' is invalid")
    if (spec.get("username") is None) != (spec.get("password") is None):
        raise ValueError(f"'{ctx.field}' requires username and password together")
    modes = (
        int(spec.get("username") is not None)
        + int(spec.get("api_key") is not None)
        + int(spec.get("bearer_token") is not None)
    )
    if modes > 1:
        raise ValueError(f"'{ctx.field}' accepts only one authentication mode")
    if "headers" in spec and not isinstance(spec.get("headers"), Mapping):
        raise TypeError(f"'{ctx.field}.headers' must be a mapping")
    ctx.validate_positive_number(
        spec.get("request_timeout_s"), field=f"{ctx.field}.request_timeout_s"
    )


def _validate_sink(ctx: ResourceValidationContext, spec: Mapping[str, Any]) -> None:
    ctx.require_string(spec, "connector")
    ctx.require_string(spec, "index")
    if spec.get("operation", "index") not in {"index", "create"}:
        raise ValueError(f"'{ctx.field}.operation' is invalid")
    ctx.validate_positive_integer(
        spec.get("chunk_size"), field=f"{ctx.field}.chunk_size"
    )
    ctx.validate_positive_integer(
        spec.get("max_chunk_bytes"), field=f"{ctx.field}.max_chunk_bytes"
    )
    refresh = spec.get("refresh", False)
    if not isinstance(refresh, bool) and refresh != "wait_for":
        raise ValueError(f"'{ctx.field}.refresh' must be false, true, or wait_for")


def _build_connector(
    ctx: ResourceBuildContext, spec: Mapping[str, Any]
) -> ElasticsearchConnector:
    return ElasticsearchConnector(
        _hosts(spec.get("hosts"), field=f"{ctx.field}.hosts"),
        distribution=spec.get("distribution", "auto"),
        username=spec.get("username"),
        password=spec.get("password"),
        api_key=spec.get("api_key"),
        bearer_token=spec.get("bearer_token"),
        headers=ctx.mapping_value(spec.get("headers"), field=f"{ctx.field}.headers"),
        verify_certs=spec.get("verify_certs", True),
        ca_certs=spec.get("ca_certs"),
        client_cert=spec.get("client_cert"),
        client_key=spec.get("client_key"),
        request_timeout_s=spec.get("request_timeout_s", 10.0),
    )


def _build_sink(ctx: ResourceBuildContext, spec: Mapping[str, Any]):
    connector = ctx.resolve_dependency(spec, "connector")
    if not isinstance(connector, ElasticsearchConnector):
        raise TypeError(
            f"resource {spec['connector']!r} is not an ElasticsearchConnector"
        )
    return connector.bulk_sink(
        index=ctx.require_string(spec, "index"),
        operation=spec.get("operation", "index"),
        id_field=spec.get("id_field"),
        chunk_size=spec.get("chunk_size", 500),
        max_chunk_bytes=spec.get("max_chunk_bytes", 5_000_000),
        refresh=spec.get("refresh", False),
        pipeline=spec.get("pipeline"),
    )


def register_resources(registry: ResourceRegistry) -> None:
    registry.register_resource_type(
        ResourceSpecHandler(
            type="elasticsearch",
            catalog=CONNECTOR_CATALOG,
            allowed_fields=CONNECTOR_FIELDS,
            build=_build_connector,
            validate=_validate_connector,
        )
    )
    registry.register_resource_type(
        ResourceSpecHandler(
            type="elasticsearch_bulk_sink",
            catalog=SINK_CATALOG,
            allowed_fields=SINK_FIELDS,
            build=_build_sink,
            validate=_validate_sink,
        )
    )
