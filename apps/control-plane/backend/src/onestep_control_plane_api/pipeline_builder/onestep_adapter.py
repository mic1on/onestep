from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from onestep_control_plane_api.pipeline_builder.compiler import (
    PipelineCompileError,
    PipelineCompiler,
    predicate_name,
)
from onestep_control_plane_api.pipeline_builder.schemas import GraphEdge, GraphNode, PipelineGraph

ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def build_onestep_config(
    pipeline_name: str,
    graph: PipelineGraph,
    *,
    handler_module: str,
    credentials: dict[str, dict[str, Any]] | None = None,
    runtime: bool,
    compiler: PipelineCompiler | None = None,
) -> dict[str, Any]:
    active_credentials = credentials or _placeholder_credentials(graph)
    active_compiler = compiler or PipelineCompiler()
    active_compiler.compile(graph, active_credentials)

    resources: dict[str, dict[str, Any]] = {}
    for edge in graph.edges:
        resources[_edge_resource(edge)] = {"type": "memory", "maxsize": 1000}

    connection_resources: dict[tuple[str, str], str] = {}
    for node in graph.nodes:
        if _node_kind(node) == "handler":
            continue
        for name, spec in _node_resources(
            node,
            credentials=active_credentials,
            runtime=runtime,
            connection_resources=connection_resources,
        ):
            resources[name] = spec

    return {
        "apiVersion": "onestep/v1alpha1",
        "kind": "App",
        "app": {"name": _slugify(pipeline_name)},
        "resources": resources,
        "tasks": _tasks_for_graph(graph, handler_module),
    }


def build_env_example(config: Mapping[str, Any]) -> str:
    names = sorted(_collect_env_refs(config))
    return "".join(f"{name}=\n" for name in names)


def build_requirements(graph: PipelineGraph) -> list[str]:
    extras = {"yaml"}
    packages: set[str] = set()
    for node in graph.nodes:
        connector = _connector_family(node.type)
        if connector in {"mysql", "rabbitmq", "redis", "sqs"}:
            extras.add(connector)
        elif connector == "postgres":
            packages.add("onestep-postgres")
        elif connector == "feishu_bitable":
            packages.add("onestep-feishu-bitable")
    return [f"onestep[{','.join(sorted(extras))}]", *sorted(packages)]


def _node_resources(
    node: GraphNode,
    *,
    credentials: dict[str, dict[str, Any]],
    runtime: bool,
    connection_resources: dict[tuple[str, str], str],
) -> list[tuple[str, dict[str, Any]]]:
    if node.type in {"cron_source", "interval_source", "webhook_source", "http_sink"}:
        return [(_node_resource(node), _builtin_resource_spec(node))]

    family = _connector_family(node.type)
    connection_name, is_new_connection = _connection_resource(
        node,
        family=family,
        connection_resources=connection_resources,
    )
    specs: list[tuple[str, dict[str, Any]]] = []
    if is_new_connection:
        connection_spec = _connection_spec(node, family=family, credentials=credentials, runtime=runtime)
        if connection_spec is not None:
            specs.append((connection_name, connection_spec))
    specs.append((_node_resource(node), _connector_node_spec(node, family, connection_name)))
    return specs


def _connection_resource(
    node: GraphNode,
    *,
    family: str,
    connection_resources: dict[tuple[str, str], str],
) -> tuple[str, bool]:
    key = (family, node.credential_ref or node.id)
    existing = connection_resources.get(key)
    if existing is not None:
        return existing, False
    if node.credential_ref:
        name = f"cred_{_safe_key(node.credential_ref)}"
    else:
        name = f"conn_{_safe_key(node.id)}"
    connection_resources[key] = name
    return name, True


def _connection_spec(
    node: GraphNode,
    *,
    family: str,
    credentials: dict[str, dict[str, Any]],
    runtime: bool,
) -> dict[str, Any] | None:
    raw_config = _connection_config(node, credentials)
    if runtime:
        raw_config = _resolve_runtime_config(raw_config, _credential_env_vars(node, credentials))
    elif node.credential_ref:
        raw_config = _prefix_credential_env_refs(raw_config, node.credential_ref)

    if family == "rabbitmq":
        return {"type": "rabbitmq", "url": _first_config_value(raw_config, "url", "dsn")}
    if family == "mysql":
        return {"type": "mysql", "dsn": _sync_mysql_dsn(_first_config_value(raw_config, "dsn", "url"))}
    if family == "postgres":
        return {"type": "postgres", "dsn": _sync_postgres_dsn(_first_config_value(raw_config, "dsn", "url"))}
    if family == "redis":
        return {"type": "redis", "url": _first_config_value(raw_config, "url", "dsn")}
    if family == "sqs":
        spec = {
            "type": "sqs",
            "region_name": raw_config.get("region_name", "${AWS_REGION:-us-east-1}"),
        }
        _copy_optional(spec, raw_config, "options")
        return spec
    if family == "feishu_bitable":
        return {"type": "feishu_bitable", **raw_config}
    return None


def _connector_node_spec(node: GraphNode, family: str, connection_name: str) -> dict[str, Any]:
    config = dict(node.config)
    if family == "rabbitmq":
        spec = {
            "type": "rabbitmq_queue",
            "connector": connection_name,
            "queue": config.get("queue", node.id),
        }
        _copy_optional(spec, config, "durable", "prefetch")
        return spec
    if family == "mysql" and _node_kind(node) == "source":
        mode = str(config.get("mode", "incremental")).strip().lower()
        if mode == "table_queue":
            spec = {
                "type": "mysql_table_queue",
                "connector": connection_name,
                "table": config.get("table", node.id),
            }
        elif mode == "binlog":
            spec = {
                "type": "mysql_binlog",
                "connector": connection_name,
                "table": config.get("table", node.id),
            }
        else:
            spec = {
                "type": "mysql_incremental",
                "connector": connection_name,
                "table": config.get("table", node.id),
                "key": config.get("key", "id"),
                "cursor": config.get("cursor", [config.get("cursor_column", "updated_at"), "id"]),
            }
        return spec
    if family == "mysql":
        spec = {
            "type": "mysql_table_sink",
            "connector": connection_name,
            "table": config.get("table", node.id),
            "mode": config.get("mode", "insert"),
        }
        if "keys" in config:
            spec["keys"] = _string_list(config["keys"])
        return spec
    if family == "postgres" and _node_kind(node) == "source":
        mode = str(config.get("mode", "incremental")).strip().lower()
        if mode == "table_queue":
            spec = {
                "type": "postgres_table_queue",
                "connector": connection_name,
                "table": config.get("table", node.id),
                "key": config.get("key", "id"),
                "where": config.get("where", "status = 'pending'"),
                "claim": _mapping_config(config, "claim", {"status": "processing"}),
                "ack": _mapping_config(config, "ack", {"status": "done"}),
            }
            nack = _optional_mapping_config(config, "nack")
            if nack is not None:
                spec["nack"] = nack
            _copy_optional(spec, config, "batch_size", "poll_interval_s")
            return spec
        spec = {
            "type": "postgres_incremental",
            "connector": connection_name,
            "table": config.get("table", node.id),
            "key": config.get("key", "id"),
            "cursor": _postgres_cursor(config),
        }
        _copy_optional(spec, config, "where", "batch_size", "poll_interval_s", "state", "state_key")
        return spec
    if family == "postgres":
        spec = {
            "type": "postgres_table_sink",
            "connector": connection_name,
            "table": config.get("table", node.id),
            "mode": config.get("mode", "insert"),
        }
        if "keys" in config:
            spec["keys"] = _string_list(config["keys"])
        return spec
    if family == "redis":
        spec = {
            "type": "redis_stream",
            "connector": connection_name,
            "stream": config.get("stream", config.get("queue", node.id)),
        }
        _copy_optional(spec, config, "group", "consumer", "create_group")
        return spec
    if family == "sqs":
        spec = {
            "type": "sqs_queue",
            "connector": connection_name,
            "url": config.get("url", config.get("queue_url", "${SQS_QUEUE_URL}")),
        }
        _copy_optional(spec, config, "wait_time_s", "visibility_timeout")
        return spec
    if family == "feishu_bitable":
        if _node_kind(node) == "source":
            return {
                "type": "feishu_bitable_incremental",
                "connector": connection_name,
                "app_token": config.get("app_token", "${FEISHU_APP_TOKEN}"),
                "table_id": config.get("table_id", "${FEISHU_TABLE_ID}"),
                "cursor_field": config.get("cursor_field", "updated_at"),
                **_only_present(
                    config,
                    "batch_size",
                    "poll_interval_s",
                    "fallback_scan_page_limit",
                    "state",
                    "state_key",
                    "user_id_type",
                ),
            }
        spec = {
            "type": "feishu_bitable_table_sink",
            "connector": connection_name,
            "app_token": config.get("app_token", "${FEISHU_APP_TOKEN}"),
            "table_id": config.get("table_id", "${FEISHU_TABLE_ID}"),
            "mode": config.get("mode", "upsert"),
            **_only_present(config, "user_id_type"),
        }
        if "match_fields" in config:
            spec["match_fields"] = _string_list(config["match_fields"])
        return spec
    raise PipelineCompileError(f"node type {node.type} is not mapped to a OneStep resource")


def _builtin_resource_spec(node: GraphNode) -> dict[str, Any]:
    config = dict(node.config)
    if node.type == "cron_source":
        spec = {
            "type": "cron",
            "expression": config.get("expression", "0 * * * *"),
            "overlap": config.get("overlap", "skip"),
        }
        _copy_optional(
            spec,
            config,
            "payload",
            "immediate",
            "timezone",
            "timezone_name",
            "poll_interval_s",
            "max_queued_runs",
        )
        return spec
    if node.type == "interval_source":
        spec = {
            "type": "interval",
            "seconds": config.get("seconds", 60),
            "overlap": config.get("overlap", "skip"),
        }
        _copy_optional(
            spec,
            config,
            "days",
            "hours",
            "minutes",
            "payload",
            "immediate",
            "timezone",
            "timezone_name",
            "poll_interval_s",
            "max_queued_runs",
        )
        return spec
    if node.type == "webhook_source":
        spec = {
            "type": "webhook",
            "path": config.get("path", f"/webhooks/{node.id}"),
            "methods": _string_list(config.get("methods", ["POST"])),
        }
        _copy_optional(
            spec,
            config,
            "host",
            "port",
            "parser",
            "auth",
            "response",
            "max_body_bytes",
            "read_timeout_s",
            "queue_maxsize",
            "batch_size",
            "poll_interval_s",
        )
        return spec
    if node.type == "http_sink":
        spec = {
            "type": "http_sink",
            "url": config.get("url", "https://example.com/onestep"),
            "method": config.get("method", "POST"),
        }
        _copy_optional(spec, config, "headers", "params", "timeout_s", "success_statuses")
        return spec
    raise PipelineCompileError(f"node type {node.type} is not mapped to a built-in resource")


def _tasks_for_graph(graph: PipelineGraph, handler_module: str) -> list[dict[str, Any]]:
    incoming = _incoming_edges(graph)
    outgoing = _outgoing_edges(graph)
    tasks: list[dict[str, Any]] = []
    for node in graph.nodes:
        kind = _node_kind(node)
        if kind == "source":
            tasks.append(
                {
                    "name": node.id,
                    "source": _node_resource(node),
                    "emit": [_edge_resource(edge) for edge in outgoing[node.id]],
                    "config": {"node_id": node.id, "node_type": node.type},
                }
            )
            continue

        for edge in incoming[node.id]:
            task = {
                "name": node.id if len(incoming[node.id]) == 1 else f"{node.id}__from__{edge.from_}",
                "source": _edge_resource(edge),
                "config": {"node_id": node.id, "node_type": node.type},
            }
            if kind == "handler":
                task["handler"] = {"ref": f"{handler_module}:{_handler_name(node.id)}"}
                emits = [_emit_entry(out_edge, handler_module) for out_edge in outgoing[node.id]]
                if emits:
                    task["emit"] = emits
            else:
                task["emit"] = [_node_resource(node)]
            tasks.append(task)
    return tasks


def _connection_config(node: GraphNode, credentials: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if node.credential_ref and node.credential_ref in credentials:
        raw = credentials[node.credential_ref].get("config", {})
        if isinstance(raw, Mapping):
            return dict(raw)
    keys = {
        "url",
        "dsn",
        "region_name",
        "access_key_id",
        "secret_access_key",
        "token",
        "app_id",
        "app_secret",
        "options",
    }
    return {key: value for key, value in node.config.items() if key in keys}


def _credential_env_vars(
    node: GraphNode,
    credentials: dict[str, dict[str, Any]],
) -> dict[str, str]:
    if not node.credential_ref or node.credential_ref not in credentials:
        return {}
    raw = credentials[node.credential_ref].get("env_vars", {})
    if not isinstance(raw, Mapping):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _resolve_runtime_config(value: Any, env_vars: dict[str, str]) -> Any:
    if isinstance(value, str):
        return interpolate_env_vars(value, env_vars)
    if isinstance(value, Mapping):
        return {key: _resolve_runtime_config(item, env_vars) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_runtime_config(item, env_vars) for item in value]
    return value


def interpolate_env_vars(value: str, env_vars: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in env_vars:
            raise KeyError(f"missing env var {name}")
        return env_vars[name]

    return ENV_VAR_PATTERN.sub(replace, value)


def _prefix_credential_env_refs(value: Any, credential_ref: str) -> Any:
    prefix = _safe_key(credential_ref).upper()

    def replace(match: re.Match[str]) -> str:
        return "${" + f"{prefix}_{match.group(1)}" + "}"

    if isinstance(value, str):
        return ENV_VAR_PATTERN.sub(replace, value)
    if isinstance(value, Mapping):
        return {key: _prefix_credential_env_refs(item, credential_ref) for key, item in value.items()}
    if isinstance(value, list):
        return [_prefix_credential_env_refs(item, credential_ref) for item in value]
    return value


def _collect_env_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, str):
        refs.update(match.group(1).split(":", 1)[0] for match in ENV_VAR_PATTERN.finditer(value))
    elif isinstance(value, Mapping):
        for item in value.values():
            refs.update(_collect_env_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(_collect_env_refs(item))
    return refs


def _placeholder_credentials(graph: PipelineGraph) -> dict[str, dict[str, Any]]:
    return {
        node.credential_ref: {"config": {}, "env_vars": {}}
        for node in graph.nodes
        if node.credential_ref
    }


def _incoming_edges(graph: PipelineGraph) -> dict[str, list[GraphEdge]]:
    incoming: dict[str, list[GraphEdge]] = defaultdict(list)
    for node in graph.nodes:
        incoming[node.id] = []
    for edge in graph.edges:
        incoming[edge.to].append(edge)
    return incoming


def _outgoing_edges(graph: PipelineGraph) -> dict[str, list[GraphEdge]]:
    outgoing: dict[str, list[GraphEdge]] = defaultdict(list)
    for node in graph.nodes:
        outgoing[node.id] = []
    for edge in graph.edges:
        outgoing[edge.from_].append(edge)
    return outgoing


def _node_kind(node: GraphNode) -> str:
    if node.kind:
        return node.kind
    if node.type == "handler":
        return "handler"
    if node.type.endswith("_sink"):
        return "sink"
    return "source"


def _connector_family(node_type: str) -> str:
    if node_type.startswith("rabbitmq_"):
        return "rabbitmq"
    if node_type.startswith("mysql_"):
        return "mysql"
    if node_type.startswith("postgres_"):
        return "postgres"
    if node_type.startswith("redis_"):
        return "redis"
    if node_type.startswith("sqs_"):
        return "sqs"
    if node_type.startswith("feishu_bitable_"):
        return "feishu_bitable"
    return node_type.removesuffix("_source").removesuffix("_sink")


def _node_resource(node: GraphNode) -> str:
    return f"node_{_safe_key(node.id)}"


def _edge_resource(edge: GraphEdge) -> str:
    return f"edge_{_safe_key(edge.from_)}__{_safe_key(edge.to)}"


def _emit_entry(edge: GraphEdge, handler_module: str) -> str | dict[str, Any]:
    condition = edge.condition.strip() if edge.condition else ""
    if not condition:
        return _edge_resource(edge)
    return {
        "when": f"{handler_module}:{predicate_name(edge)}",
        "then": _edge_resource(edge),
    }


def _handler_name(node_id: str) -> str:
    return f"handler_{_safe_key(node_id)}"


def _safe_key(value: str) -> str:
    key = re.sub(r"[^0-9A-Za-z_]+", "_", value.strip()).strip("_")
    return key or "resource"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z_]+", "_", value.strip().lower()).strip("_")
    return slug or "onestep_worker"


def _first_config_value(config: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = config.get(key)
        if value:
            return value
    return "${" + keys[0].upper() + "}"


def _sync_mysql_dsn(dsn: Any) -> Any:
    if isinstance(dsn, str) and dsn.startswith("mysql://"):
        return "mysql+pymysql://" + dsn.removeprefix("mysql://")
    return dsn


def _sync_postgres_dsn(dsn: Any) -> Any:
    if isinstance(dsn, str) and dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn.removeprefix("postgresql://")
    if isinstance(dsn, str) and dsn.startswith("postgres://"):
        return "postgresql+psycopg://" + dsn.removeprefix("postgres://")
    return dsn


def _copy_optional(target: dict[str, Any], source: Mapping[str, Any], *keys: str) -> None:
    for key in keys:
        if key in source and source[key] is not None:
            target[key] = source[key]


def _only_present(source: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {key: source[key] for key in keys if key in source and source[key] is not None}


def _postgres_cursor(config: Mapping[str, Any]) -> list[str]:
    key = str(config.get("key", "id"))
    raw_cursor = config.get("cursor")
    if raw_cursor is None:
        raw_cursor = [config.get("cursor_column", "updated_at"), key]
    cursor = _string_list(raw_cursor)
    return cursor if key in cursor else [*cursor, key]


def _mapping_config(config: Mapping[str, Any], key: str, default: dict[str, Any]) -> dict[str, Any]:
    value = _optional_mapping_config(config, key)
    return value if value is not None else dict(default)


def _optional_mapping_config(config: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    if key not in config or config[key] is None:
        return None
    value = config[key]
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise PipelineCompileError(f"{key} must be a JSON object") from exc
        if isinstance(parsed, Mapping):
            return dict(parsed)
    raise PipelineCompileError(f"{key} must be a JSON object")


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []
