from __future__ import annotations

import copy
import functools
import importlib
import inspect
import os
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .app import OneStepApp
from .connectors.base import Sink, Source
from .connectors.memory import MemoryQueue
from .connectors.mysql import MySQLConnector
from .connectors.rabbitmq import RabbitMQConnector
from .connectors.redis import RedisConnector
from .connectors.schedule import CronSource, IntervalSource
from .connectors.sqs import SQSConnector
from .connectors.webhook import BearerAuth, WebhookResponse, WebhookSource
from .reporter import ControlPlaneReporter, ControlPlaneReporterConfig
from .retry import MaxAttempts, NoRetry, RetryPolicy
from .task import TaskHooks

_YAML_SUFFIXES = (".yaml", ".yml")

# Pattern for ${VAR} or ${VAR:-default} or ${VAR:default}
_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")

_STRICT_API_VERSION = "onestep/v1alpha1"
_STRICT_KIND = "App"
_LEGACY_APP_FIELDS = frozenset({"name", "shutdown_timeout_s", "config", "state"})
_STRICT_TOP_LEVEL_FIELDS = frozenset(
    {
        "apiVersion",
        "kind",
        "app",
        "reporter",
        "resources",
        "connectors",
        "sources",
        "sinks",
        "hooks",
        "tasks",
        *_LEGACY_APP_FIELDS,
    }
)
_STRICT_APP_FIELDS = frozenset({"name", "shutdown_timeout_s", "config", "state"})
_STRICT_HANDLER_FIELDS = frozenset({"ref", "params"})
_STRICT_APP_HOOK_FIELDS = frozenset({"startup", "shutdown", "events"})
_STRICT_TASK_HOOK_FIELDS = frozenset({"before", "after_success", "on_failure"})
_STRICT_TASK_FIELDS = frozenset(
    {
        "name",
        "description",
        "source",
        "emit",
        "dead_letter",
        "config",
        "metadata",
        "handler",
        "hooks",
        "concurrency",
        "retry",
        "timeout_s",
    }
)
_STRICT_REPORTER_FIELDS = frozenset({"base_url", "token", "service_name"})
_STRICT_WEBHOOK_AUTH_FIELDS = frozenset({"type", "token", "header", "scheme", "realm"})
_STRICT_WEBHOOK_RESPONSE_FIELDS = frozenset({"status_code", "body", "headers"})
_STRICT_RESOURCE_FIELDS: dict[str, frozenset[str]] = {
    "memory": frozenset({"type", "name", "maxsize", "batch_size", "poll_interval_s"}),
    "interval": frozenset(
        {
            "type",
            "name",
            "days",
            "hours",
            "minutes",
            "seconds",
            "payload",
            "immediate",
            "overlap",
            "timezone",
            "timezone_name",
            "poll_interval_s",
        }
    ),
    "cron": frozenset(
        {
            "type",
            "name",
            "expression",
            "payload",
            "immediate",
            "overlap",
            "timezone",
            "timezone_name",
            "poll_interval_s",
        }
    ),
    "webhook": frozenset(
        {
            "type",
            "name",
            "path",
            "methods",
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
        }
    ),
    "rabbitmq": frozenset({"type", "url", "options"}),
    "rabbitmq_queue": frozenset(
        {
            "type",
            "name",
            "queue",
            "connector",
            "routing_key",
            "exchange",
            "exchange_type",
            "bind",
            "bind_arguments",
            "durable",
            "auto_delete",
            "exclusive",
            "arguments",
            "exchange_durable",
            "exchange_auto_delete",
            "exchange_arguments",
            "prefetch",
            "batch_size",
            "poll_interval_s",
            "publisher_confirms",
            "persistent",
        }
    ),
    "redis": frozenset({"type", "url", "options"}),
    "redis_stream": frozenset(
        {
            "type",
            "name",
            "stream",
            "connector",
            "group",
            "consumer",
            "batch_size",
            "poll_interval_s",
            "block_ms",
            "start_id",
            "create_group",
            "maxlen",
            "approximate_trim",
        }
    ),
    "sqs": frozenset({"type", "region_name", "options"}),
    "sqs_queue": frozenset(
        {
            "type",
            "name",
            "url",
            "connector",
            "wait_time_s",
            "visibility_timeout",
            "batch_size",
            "poll_interval_s",
            "message_group_id",
            "deduplication_id_factory",
            "on_fail",
            "delete_batch_size",
            "delete_flush_interval_s",
            "heartbeat_interval_s",
            "heartbeat_visibility_timeout",
        }
    ),
    "mysql": frozenset({"type", "dsn", "engine_options"}),
    "mysql_state_store": frozenset(
        {"type", "connector", "table", "key_column", "value_column", "updated_at_column", "auto_create"}
    ),
    "mysql_cursor_store": frozenset(
        {"type", "connector", "table", "key_column", "value_column", "updated_at_column", "auto_create"}
    ),
    "mysql_table_queue": frozenset(
        {"type", "connector", "table", "key", "where", "claim", "ack", "nack", "batch_size", "poll_interval_s"}
    ),
    "mysql_incremental": frozenset(
        {"type", "connector", "table", "key", "cursor", "where", "batch_size", "poll_interval_s", "state", "state_key"}
    ),
    "mysql_table_sink": frozenset({"type", "connector", "table", "mode", "keys"}),
}
_STRICT_RETRY_FIELDS: dict[str, frozenset[str]] = {
    "no_retry": frozenset({"type"}),
    "none": frozenset({"type"}),
    "max_attempts": frozenset({"type", "max_attempts", "delay_s"}),
}


def _expand_env_var(match: re.Match[str]) -> str:
    """Expand a single environment variable reference.
    
    Supports:
    - ${VAR} - use VAR from environment, empty if not set
    - ${VAR:-default} - use VAR from environment, or default if not set
    - ${VAR:default} - use VAR from environment, or default if not set
    """
    content = match.group(1)
    
    # Check for default value with :- syntax
    if ":-" in content:
        var_name, default = content.split(":-", 1)
        return os.environ.get(var_name, default)
    
    # Check for default value with : syntax
    if ":" in content:
        var_name, default = content.split(":", 1)
        return os.environ.get(var_name, default)
    
    # No default, just the variable name
    return os.environ.get(content, "")


def _expand_env_vars_in_string(value: str) -> str:
    """Expand all environment variable references in a string."""
    return _ENV_VAR_PATTERN.sub(_expand_env_var, value)


def _expand_env_vars(value: Any) -> Any:
    """Recursively expand environment variables in a configuration value.
    
    Supports ${VAR}, ${VAR:-default}, ${VAR:default} syntax in strings.
    """
    if isinstance(value, str):
        return _expand_env_vars_in_string(value)
    if isinstance(value, Mapping):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(item) for item in value]
    return value


def is_yaml_target(target: str) -> bool:
    return target.lower().endswith(_YAML_SUFFIXES)


def load_yaml_app(path: str, *, strict: bool = False) -> OneStepApp:
    yaml = _import_yaml()
    resolved_path = os.path.abspath(path)
    with open(resolved_path, "r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, Mapping):
        raise TypeError("YAML app config must be a mapping at the top level")
    # Expand environment variables in the loaded config
    expanded = _expand_env_vars(loaded)
    return load_app_config(expanded, source_path=resolved_path, strict=strict)


def load_app_config(
    config: Mapping[str, Any],
    *,
    source_path: str | None = None,
    strict: bool = False,
) -> OneStepApp:
    if strict:
        validate_app_config(config)
    app_section = config.get("app")
    if app_section is None:
        app_name = _require_string(config, "name")
        shutdown_timeout_s = config.get("shutdown_timeout_s", 30.0)
        app_config = config.get("config")
        app_state = config.get("state")
    else:
        if not isinstance(app_section, Mapping):
            raise TypeError("'app' must be a mapping")
        app_name = _require_string(app_section, "name")
        shutdown_timeout_s = app_section.get("shutdown_timeout_s", 30.0)
        app_config = app_section.get("config")
        app_state = app_section.get("state")

    if app_config is None:
        app_config = {}
    if not isinstance(app_config, Mapping):
        raise TypeError("'config' must be a mapping when provided")

    app = OneStepApp(
        app_name,
        config=dict(app_config),
        shutdown_timeout_s=shutdown_timeout_s,
    )
    if source_path is not None:
        app.config.setdefault("config_path", source_path)

    resources = _build_resources(config)
    app.bind_resources(resources)
    if app_state is not None:
        app.state = _resolve_app_state(resources, app_state)
    tasks = config.get("tasks", [])
    if not isinstance(tasks, Sequence) or isinstance(tasks, (str, bytes)):
        raise TypeError("'tasks' must be a list")

    for index, task_config in enumerate(tasks):
        if not isinstance(task_config, Mapping):
            raise TypeError(f"'tasks[{index}]' must be a mapping")
        task_name = _optional_string(task_config, "name")
        handler, handler_ref = _build_handler(task_config.get("handler"), task_name=task_name)
        app.task(
            name=task_name,
            description=_optional_string(task_config, "description"),
            source=_resolve_optional_source(resources, task_config.get("source"), task_index=index),
            emit=_resolve_optional_sinks(resources, task_config.get("emit"), field="emit", task_index=index),
            dead_letter=_resolve_optional_sinks(
                resources,
                task_config.get("dead_letter"),
                field="dead_letter",
                task_index=index,
            ),
            config=_mapping_value(task_config.get("config"), field=f"tasks[{index}].config"),
            metadata=_mapping_value(task_config.get("metadata"), field=f"tasks[{index}].metadata"),
            handler_ref=handler_ref,
            hooks=_build_task_hooks(task_config.get("hooks"), task_index=index),
            concurrency=task_config.get("concurrency", 1),
            retry=_build_retry(task_config.get("retry")),
            timeout_s=task_config.get("timeout_s"),
        )(handler)

    _register_app_hooks(app, config.get("hooks"))
    _attach_reporter(app, config.get("reporter"))
    return app


def _build_resources(config: Mapping[str, Any]) -> dict[str, Any]:
    specs = _collect_resource_specs(config)
    resources: dict[str, Any] = {}
    resolving: list[str] = []

    def resolve(name: str) -> Any:
        if name in resources:
            return resources[name]
        try:
            spec = specs[name]
        except KeyError as exc:
            raise KeyError(f"unknown resource {name!r}") from exc
        if name in resolving:
            cycle = " -> ".join([*resolving, name])
            raise ValueError(f"cyclic resource reference detected: {cycle}")
        resolving.append(name)
        try:
            built = _build_resource(name, spec, resolve=resolve)
            resources[name] = built
            return built
        finally:
            resolving.pop()

    for name in specs:
        resolve(name)
    return resources


def _collect_resource_specs(config: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    specs: dict[str, dict[str, Any]] = {}
    for section_name in ("resources", "connectors", "sources", "sinks"):
        section = config.get(section_name, {})
        if section is None:
            continue
        if not isinstance(section, Mapping):
            raise TypeError(f"'{section_name}' must be a mapping")
        for name, raw_spec in section.items():
            if not isinstance(raw_spec, Mapping):
                raise TypeError(f"'{section_name}.{name}' must be a mapping")
            spec = dict(raw_spec)
            if name in specs:
                if specs[name] != spec:
                    raise ValueError(f"resource {name!r} is defined multiple times with different configs")
                continue
            specs[name] = spec
    return specs


def _build_resource(name: str, spec: Mapping[str, Any], *, resolve: Callable[[str], Any]) -> Any:
    resource_type = _normalize_resource_type(_require_string(spec, "type"))
    resource_name = _resource_name(spec, fallback=name)

    if resource_type == "memory":
        return MemoryQueue(
            resource_name,
            maxsize=spec.get("maxsize", 0),
            batch_size=spec.get("batch_size", 100),
            poll_interval_s=spec.get("poll_interval_s", 0.1),
        )
    if resource_type == "interval":
        return IntervalSource.every(
            days=spec.get("days", 0),
            hours=spec.get("hours", 0),
            minutes=spec.get("minutes", 0),
            seconds=spec.get("seconds", 0),
            payload=spec.get("payload"),
            immediate=spec.get("immediate", False),
            overlap=spec.get("overlap", "allow"),
            timezone=spec.get("timezone"),
            timezone_name=spec.get("timezone_name"),
            poll_interval_s=spec.get("poll_interval_s"),
            name=resource_name,
        )
    if resource_type == "cron":
        return CronSource(
            _require_string(spec, "expression"),
            payload=spec.get("payload"),
            immediate=spec.get("immediate", False),
            overlap=spec.get("overlap", "allow"),
            timezone=spec.get("timezone"),
            timezone_name=spec.get("timezone_name"),
            poll_interval_s=spec.get("poll_interval_s", 1.0),
            name=resource_name,
        )
    if resource_type == "webhook":
        methods = spec.get("methods", ("POST",))
        return WebhookSource(
            path=_require_string(spec, "path"),
            methods=tuple(_string_list(methods, field=f"resources.{name}.methods")),
            host=spec.get("host", "127.0.0.1"),
            port=spec.get("port", 8080),
            parser=spec.get("parser", "auto"),
            auth=_build_webhook_auth(spec.get("auth")),
            response=_build_webhook_response(spec.get("response")),
            max_body_bytes=spec.get("max_body_bytes", 1024 * 1024),
            read_timeout_s=spec.get("read_timeout_s", 5.0),
            queue_maxsize=spec.get("queue_maxsize", 1000),
            batch_size=spec.get("batch_size", 100),
            poll_interval_s=spec.get("poll_interval_s", 0.1),
            name=resource_name,
        )
    if resource_type == "rabbitmq":
        return RabbitMQConnector(
            _require_string(spec, "url"),
            options=_mapping_value(spec.get("options"), field=f"resources.{name}.options"),
        )
    if resource_type == "rabbitmq_queue":
        connector = _resolve_dependency(resolve, spec, "connector")
        if not hasattr(connector, "queue"):
            raise TypeError(f"resource {spec['connector']!r} cannot build rabbitmq_queue")
        queue_name = _resource_name(spec, fallback=name, key="queue")
        return connector.queue(
            queue_name,
            routing_key=spec.get("routing_key"),
            exchange=spec.get("exchange"),
            exchange_type=spec.get("exchange_type", "direct"),
            bind=spec.get("bind", True),
            bind_arguments=_mapping_value(spec.get("bind_arguments"), field=f"resources.{name}.bind_arguments"),
            durable=spec.get("durable", True),
            auto_delete=spec.get("auto_delete", False),
            exclusive=spec.get("exclusive", False),
            arguments=_mapping_value(spec.get("arguments"), field=f"resources.{name}.arguments"),
            exchange_durable=spec.get("exchange_durable"),
            exchange_auto_delete=spec.get("exchange_auto_delete", False),
            exchange_arguments=_mapping_value(
                spec.get("exchange_arguments"),
                field=f"resources.{name}.exchange_arguments",
            ),
            prefetch=spec.get("prefetch", 100),
            batch_size=spec.get("batch_size", 100),
            poll_interval_s=spec.get("poll_interval_s", 1.0),
            publisher_confirms=spec.get("publisher_confirms", True),
            persistent=spec.get("persistent", True),
        )
    if resource_type == "redis":
        return RedisConnector(
            _require_string(spec, "url"),
            options=_mapping_value(spec.get("options"), field=f"resources.{name}.options"),
        )
    if resource_type == "redis_stream":
        connector = _resolve_dependency(resolve, spec, "connector")
        if not hasattr(connector, "stream"):
            raise TypeError(f"resource {spec['connector']!r} cannot build redis_stream")
        stream_name = _resource_name(spec, fallback=name, key="stream")
        return connector.stream(
            stream_name,
            group=spec.get("group", "onestep"),
            consumer=spec.get("consumer"),
            batch_size=spec.get("batch_size", 100),
            poll_interval_s=spec.get("poll_interval_s", 1.0),
            block_ms=spec.get("block_ms"),
            start_id=spec.get("start_id", "$"),
            create_group=spec.get("create_group", True),
            maxlen=spec.get("maxlen"),
            approximate_trim=spec.get("approximate_trim", True),
        )
    if resource_type == "sqs":
        return SQSConnector(
            region_name=spec.get("region_name"),
            options=_mapping_value(spec.get("options"), field=f"resources.{name}.options"),
        )
    if resource_type == "sqs_queue":
        connector = _resolve_dependency(resolve, spec, "connector")
        if not hasattr(connector, "queue"):
            raise TypeError(f"resource {spec['connector']!r} cannot build sqs_queue")
        return connector.queue(
            _require_string(spec, "url"),
            wait_time_s=spec.get("wait_time_s", 20),
            visibility_timeout=spec.get("visibility_timeout"),
            batch_size=spec.get("batch_size", 10),
            poll_interval_s=spec.get("poll_interval_s", 0.0),
            message_group_id=spec.get("message_group_id"),
            deduplication_id_factory=_optional_ref(
                spec.get("deduplication_id_factory"),
                field=f"resources.{name}.deduplication_id_factory",
            ),
            on_fail=spec.get("on_fail", "leave"),
            delete_batch_size=spec.get("delete_batch_size", 10),
            delete_flush_interval_s=spec.get("delete_flush_interval_s", 0.5),
            heartbeat_interval_s=spec.get("heartbeat_interval_s"),
            heartbeat_visibility_timeout=spec.get("heartbeat_visibility_timeout"),
        )
    if resource_type == "mysql":
        return MySQLConnector(
            _require_string(spec, "dsn"),
            **_mapping_value(spec.get("engine_options"), field=f"resources.{name}.engine_options"),
        )
    if resource_type == "mysql_state_store":
        connector = _resolve_dependency(resolve, spec, "connector")
        if not hasattr(connector, "state_store"):
            raise TypeError(f"resource {spec['connector']!r} cannot build mysql_state_store")
        return connector.state_store(
            table=spec.get("table", "onestep_state"),
            key_column=spec.get("key_column", "state_key"),
            value_column=spec.get("value_column", "state_value"),
            updated_at_column=spec.get("updated_at_column", "updated_at"),
            auto_create=spec.get("auto_create", True),
        )
    if resource_type == "mysql_cursor_store":
        connector = _resolve_dependency(resolve, spec, "connector")
        if not hasattr(connector, "cursor_store"):
            raise TypeError(f"resource {spec['connector']!r} cannot build mysql_cursor_store")
        return connector.cursor_store(
            table=spec.get("table", "onestep_cursor"),
            key_column=spec.get("key_column", "cursor_key"),
            value_column=spec.get("value_column", "cursor_value"),
            updated_at_column=spec.get("updated_at_column", "updated_at"),
            auto_create=spec.get("auto_create", True),
        )
    if resource_type == "mysql_table_queue":
        connector = _resolve_dependency(resolve, spec, "connector")
        if not hasattr(connector, "table_queue"):
            raise TypeError(f"resource {spec['connector']!r} cannot build mysql_table_queue")
        return connector.table_queue(
            table=_require_string(spec, "table"),
            key=_require_string(spec, "key"),
            where=_require_string(spec, "where"),
            claim=_require_mapping(spec, "claim"),
            ack=_require_mapping(spec, "ack"),
            nack=_optional_mapping(spec.get("nack"), field=f"resources.{name}.nack") or None,
            batch_size=spec.get("batch_size", 100),
            poll_interval_s=spec.get("poll_interval_s", 1.0),
        )
    if resource_type == "mysql_incremental":
        connector = _resolve_dependency(resolve, spec, "connector")
        if not hasattr(connector, "incremental"):
            raise TypeError(f"resource {spec['connector']!r} cannot build mysql_incremental")
        raw_state_name = spec.get("state")
        state = None
        if raw_state_name is not None:
            state_name = _string_value(raw_state_name, field=f"resources.{name}.state")
            state = resolve(state_name)
            if not _is_cursor_store(state):
                raise TypeError(f"resource {state_name!r} cannot be used as incremental state")
        return connector.incremental(
            table=_require_string(spec, "table"),
            key=_require_string(spec, "key"),
            cursor=tuple(_string_list(spec.get("cursor"), field=f"resources.{name}.cursor")),
            where=spec.get("where"),
            batch_size=spec.get("batch_size", 1000),
            poll_interval_s=spec.get("poll_interval_s", 1.0),
            state=state,
            state_key=spec.get("state_key"),
        )
    if resource_type == "mysql_table_sink":
        connector = _resolve_dependency(resolve, spec, "connector")
        if not hasattr(connector, "table_sink"):
            raise TypeError(f"resource {spec['connector']!r} cannot build mysql_table_sink")
        keys = spec.get("keys")
        return connector.table_sink(
            table=_require_string(spec, "table"),
            mode=spec.get("mode", "insert"),
            keys=tuple(_string_list(keys, field=f"resources.{name}.keys")) if keys is not None else (),
        )

    raise ValueError(f"unsupported resource type {resource_type!r} for resource {name!r}")


def _build_handler(raw_handler: Any, *, task_name: str | None):
    func, ref = _resolve_callable_ref(raw_handler, field="handler")

    @functools.wraps(func)
    async def bound_handler(ctx, payload):
        result = func(ctx, payload)
        if inspect.isawaitable(result):
            return await result
        return result

    bound_handler.__name__ = task_name or getattr(func, "__name__", "configured_handler")
    bound_handler.__doc__ = getattr(func, "__doc__", None)
    return bound_handler, ref


def _build_retry(raw_retry: Any) -> RetryPolicy | None:
    if raw_retry is None:
        return None
    if isinstance(raw_retry, str):
        retry_type = raw_retry
        config: Mapping[str, Any] = {}
    elif isinstance(raw_retry, Mapping):
        retry_type = _require_string(raw_retry, "type")
        config = raw_retry
    else:
        raise TypeError("'retry' must be a string or mapping")

    normalized = _normalize_retry_type(retry_type)
    if normalized in {"no_retry", "none"}:
        return NoRetry()
    if normalized == "max_attempts":
        return MaxAttempts(
            max_attempts=config.get("max_attempts", 3),
            delay_s=config.get("delay_s"),
        )
    raise ValueError(f"unsupported retry type {retry_type!r}")


def _build_webhook_auth(raw_auth: Any) -> BearerAuth | None:
    if raw_auth is None:
        return None
    if isinstance(raw_auth, str):
        return BearerAuth(raw_auth)
    if not isinstance(raw_auth, Mapping):
        raise TypeError("'auth' must be a string or mapping")
    auth_type = str(raw_auth.get("type", "bearer")).strip().lower().replace("-", "_")
    if auth_type != "bearer":
        raise ValueError(f"unsupported webhook auth type {auth_type!r}")
    return BearerAuth(
        _require_string(raw_auth, "token"),
        header=raw_auth.get("header", "authorization"),
        scheme=raw_auth.get("scheme", "Bearer"),
        realm=raw_auth.get("realm", "webhook"),
    )


def _build_webhook_response(raw_response: Any) -> WebhookResponse | None:
    if raw_response is None:
        return None
    if not isinstance(raw_response, Mapping):
        raise TypeError("'response' must be a mapping")
    return WebhookResponse(
        status_code=raw_response.get("status_code", 202),
        body=raw_response.get("body"),
        headers=_mapping_value(raw_response.get("headers"), field="response.headers"),
    )


def _resolve_optional_source(resources: Mapping[str, Any], value: Any, *, task_index: int) -> Source | None:
    if value is None:
        return None
    name = _string_value(value, field=f"tasks[{task_index}].source")
    resolved = _resolve_resource(resources, name)
    if not isinstance(resolved, Source):
        raise TypeError(f"resource {name!r} cannot be used as a source")
    return resolved


def _resolve_optional_sinks(
    resources: Mapping[str, Any],
    value: Any,
    *,
    field: str,
    task_index: int,
) -> tuple[Sink, ...] | None:
    if value is None:
        return None
    names = _string_list(value, field=f"tasks[{task_index}].{field}")
    sinks: list[Sink] = []
    for name in names:
        resolved = _resolve_resource(resources, name)
        if not isinstance(resolved, Sink):
            raise TypeError(f"resource {name!r} cannot be used as a sink")
        sinks.append(resolved)
    return tuple(sinks)


def _resolve_app_state(resources: Mapping[str, Any], value: Any) -> Any:
    name = _string_value(value, field="app.state")
    resolved = _resolve_resource(resources, name)
    if not _is_state_store(resolved):
        raise TypeError(f"resource {name!r} cannot be used as app state")
    return resolved


def _resolve_resource(resources: Mapping[str, Any], name: str) -> Any:
    try:
        return resources[name]
    except KeyError as exc:
        raise KeyError(f"unknown resource {name!r}") from exc


def _resolve_dependency(resolve: Callable[[str], Any], spec: Mapping[str, Any], key: str) -> Any:
    return resolve(_require_string(spec, key))


def _load_ref(ref: str) -> Any:
    module_name, separator, attr_path = ref.partition(":")
    if not separator or not module_name or not attr_path:
        raise ValueError(f"invalid ref {ref!r}; expected 'package.module:attr'")
    module = importlib.import_module(module_name)
    value: Any = module
    for part in attr_path.split("."):
        value = getattr(value, part)
    return value


def _resource_name(spec: Mapping[str, Any], *, fallback: str, key: str = "name") -> str:
    value = spec.get(key)
    if value is None:
        return fallback
    return _string_value(value, field=key)


def _require_string(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' must be a non-empty string")
    return value


def _optional_string(mapping: Mapping[str, Any], key: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    return _string_value(value, field=key)


def _string_value(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{field}' must be a non-empty string")
    return value


def _string_list(value: Any, *, field: str) -> list[str]:
    if value is None:
        raise ValueError(f"'{field}' must be provided")
    if isinstance(value, str):
        return [_string_value(value, field=field)]
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError(f"'{field}' must be a string or list of strings")
    return [_string_value(item, field=field) for item in value]


def _require_mapping(mapping: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = mapping.get(key)
    if not isinstance(value, Mapping):
        raise TypeError(f"'{key}' must be a mapping")
    return dict(value)


def _optional_mapping(value: Any, *, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError(f"'{field}' must be a mapping")
    return dict(value)


def _mapping_value(value: Any, *, field: str) -> dict[str, Any]:
    return _optional_mapping(value, field=field) or {}


def _optional_ref(value: Any, *, field: str) -> Any:
    if value is None:
        return None
    if callable(value):
        return value
    if isinstance(value, str):
        return _load_ref(value)
    raise TypeError(f"'{field}' must be a callable or ref string")


def _is_cursor_store(value: Any) -> bool:
    return hasattr(value, "load") and hasattr(value, "save")


def _is_state_store(value: Any) -> bool:
    return _is_cursor_store(value) and hasattr(value, "delete")


def validate_app_config(config: Mapping[str, Any]) -> None:
    _validate_unknown_fields(config, _STRICT_TOP_LEVEL_FIELDS, field="config")

    api_version = config.get("apiVersion")
    kind = config.get("kind")
    if api_version is not None or kind is not None:
        if api_version is None or kind is None:
            raise ValueError("'apiVersion' and 'kind' must be provided together in strict mode")
        if api_version != _STRICT_API_VERSION:
            raise ValueError(f"unsupported apiVersion {api_version!r}; expected {_STRICT_API_VERSION!r}")
        if kind != _STRICT_KIND:
            raise ValueError(f"unsupported kind {kind!r}; expected {_STRICT_KIND!r}")

    app_section = config.get("app")
    if app_section is not None:
        if not isinstance(app_section, Mapping):
            raise TypeError("'app' must be a mapping")
        _validate_unknown_fields(app_section, _STRICT_APP_FIELDS, field="app")
        legacy_fields = sorted(field for field in _LEGACY_APP_FIELDS if field in config)
        if legacy_fields:
            raise ValueError(
                "strict mode does not allow mixing 'app' with legacy top-level app fields: "
                + ", ".join(legacy_fields)
            )

    _validate_reporter_config(config.get("reporter"))
    _validate_resource_sections(config)
    _validate_hooks_config(config.get("hooks"), field="hooks", allowed=_STRICT_APP_HOOK_FIELDS)
    _validate_tasks(config.get("tasks"))


def _validate_resource_sections(config: Mapping[str, Any]) -> None:
    for section_name in ("resources", "connectors", "sources", "sinks"):
        section = config.get(section_name)
        if section is None:
            continue
        if not isinstance(section, Mapping):
            raise TypeError(f"'{section_name}' must be a mapping")
        for name, raw_spec in section.items():
            if not isinstance(raw_spec, Mapping):
                raise TypeError(f"'{section_name}.{name}' must be a mapping")
            field = f"{section_name}.{name}"
            normalized_type = _normalize_resource_type(_require_string(raw_spec, "type"))
            try:
                allowed = _STRICT_RESOURCE_FIELDS[normalized_type]
            except KeyError as exc:
                raise ValueError(f"unsupported resource type {normalized_type!r} for {field}") from exc
            _validate_unknown_fields(raw_spec, allowed, field=field)
            if normalized_type == "webhook":
                _validate_webhook_resource(raw_spec, field=field)


def _validate_tasks(raw_tasks: Any) -> None:
    if raw_tasks is None:
        return
    if not isinstance(raw_tasks, Sequence) or isinstance(raw_tasks, (str, bytes)):
        raise TypeError("'tasks' must be a list")
    for index, raw_task in enumerate(raw_tasks):
        if not isinstance(raw_task, Mapping):
            raise TypeError(f"'tasks[{index}]' must be a mapping")
        field = f"tasks[{index}]"
        _validate_unknown_fields(raw_task, _STRICT_TASK_FIELDS, field=field)
        _validate_ref_entry(raw_task.get("handler"), field=f"{field}.handler")
        _validate_hooks_config(
            raw_task.get("hooks"),
            field=f"{field}.hooks",
            allowed=_STRICT_TASK_HOOK_FIELDS,
        )
        _validate_retry(raw_task.get("retry"), field=f"{field}.retry")


def _validate_reporter_config(raw_reporter: Any) -> None:
    if raw_reporter is None or raw_reporter is False or raw_reporter is True:
        return
    if not isinstance(raw_reporter, Mapping):
        raise TypeError("'reporter' must be a boolean or mapping")
    _validate_unknown_fields(raw_reporter, _STRICT_REPORTER_FIELDS, field="reporter")


def _validate_hooks_config(raw_hooks: Any, *, field: str, allowed: frozenset[str]) -> None:
    if raw_hooks is None:
        return
    if not isinstance(raw_hooks, Mapping):
        raise TypeError(f"'{field}' must be a mapping")
    _validate_unknown_fields(raw_hooks, allowed, field=field)
    for hook_name, raw_value in raw_hooks.items():
        _validate_hook_collection(raw_value, field=f"{field}.{hook_name}")


def _validate_hook_collection(raw_value: Any, *, field: str) -> None:
    if raw_value is None:
        raise ValueError(f"{field} is required")
    if isinstance(raw_value, Sequence) and not isinstance(raw_value, (str, bytes)):
        if not raw_value:
            raise ValueError(f"{field} must not be empty")
        for index, entry in enumerate(raw_value):
            _validate_ref_entry(entry, field=f"{field}[{index}]")
        return
    _validate_ref_entry(raw_value, field=field)


def _validate_ref_entry(raw_value: Any, *, field: str) -> None:
    if raw_value is None:
        raise ValueError(f"{field} is required")
    if isinstance(raw_value, str):
        _string_value(raw_value, field=field)
        return
    if not isinstance(raw_value, Mapping):
        raise TypeError(f"'{field}' must be a string ref or mapping")
    _validate_unknown_fields(raw_value, _STRICT_HANDLER_FIELDS, field=field)
    _require_string(raw_value, "ref")
    params = raw_value.get("params")
    if params is not None and not isinstance(params, Mapping):
        raise TypeError(f"'{field}.params' must be a mapping")


def _validate_retry(raw_retry: Any, *, field: str) -> None:
    if raw_retry is None:
        return
    if isinstance(raw_retry, str):
        normalized = _normalize_retry_type(raw_retry)
    elif isinstance(raw_retry, Mapping):
        retry_type = _require_string(raw_retry, "type")
        normalized = _normalize_retry_type(retry_type)
        allowed = _STRICT_RETRY_FIELDS.get(normalized)
        if allowed is None:
            raise ValueError(f"unsupported retry type {retry_type!r}")
        _validate_unknown_fields(raw_retry, allowed, field=field)
        return
    else:
        raise TypeError(f"'{field}' must be a string or mapping")
    if normalized not in _STRICT_RETRY_FIELDS:
        raise ValueError(f"unsupported retry type {raw_retry!r}")


def _validate_webhook_resource(spec: Mapping[str, Any], *, field: str) -> None:
    raw_auth = spec.get("auth")
    if raw_auth is not None:
        if isinstance(raw_auth, str):
            _string_value(raw_auth, field=f"{field}.auth")
        elif isinstance(raw_auth, Mapping):
            _validate_unknown_fields(raw_auth, _STRICT_WEBHOOK_AUTH_FIELDS, field=f"{field}.auth")
            auth_type = _normalize_retry_type(str(raw_auth.get("type", "bearer")))
            if auth_type != "bearer":
                raise ValueError(f"unsupported webhook auth type {auth_type!r}")
        else:
            raise TypeError(f"'{field}.auth' must be a string or mapping")
    raw_response = spec.get("response")
    if raw_response is not None:
        if not isinstance(raw_response, Mapping):
            raise TypeError(f"'{field}.response' must be a mapping")
        _validate_unknown_fields(raw_response, _STRICT_WEBHOOK_RESPONSE_FIELDS, field=f"{field}.response")


def _validate_unknown_fields(mapping: Mapping[str, Any], allowed: frozenset[str], *, field: str) -> None:
    unexpected = sorted(str(key) for key in mapping.keys() if key not in allowed)
    if unexpected:
        raise ValueError(f"unsupported fields for {field}: {', '.join(unexpected)}")


def _normalize_resource_type(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(".", "_")


def _normalize_retry_type(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _register_app_hooks(app: OneStepApp, raw_hooks: Any) -> None:
    if raw_hooks is None:
        return
    if not isinstance(raw_hooks, Mapping):
        raise TypeError("'hooks' must be a mapping")
    _validate_unknown_fields(raw_hooks, _STRICT_APP_HOOK_FIELDS, field="hooks")

    for hook in _build_hooks(raw_hooks.get("startup"), field="hooks.startup"):
        app.on_startup(hook)
    for hook in _build_hooks(raw_hooks.get("shutdown"), field="hooks.shutdown"):
        app.on_shutdown(hook)
    for hook in _build_hooks(raw_hooks.get("events"), field="hooks.events"):
        app.on_event(hook)


def _attach_reporter(app: OneStepApp, raw_reporter: Any) -> None:
    if raw_reporter is None or raw_reporter is False:
        return
    if raw_reporter is True:
        reporter_config = ControlPlaneReporterConfig.from_env(app_name=app.name)
    elif isinstance(raw_reporter, Mapping):
        _validate_unknown_fields(raw_reporter, _STRICT_REPORTER_FIELDS, field="reporter")
        reporter_config = ControlPlaneReporterConfig.from_env(
            app_name=app.name,
            base_url=_optional_string(raw_reporter, "base_url"),
            token=_optional_string(raw_reporter, "token"),
            service_name=_optional_string(raw_reporter, "service_name"),
        )
    else:
        raise TypeError("'reporter' must be a boolean or mapping")

    app.set_reporter_summary(
        {
            "type": "control_plane",
            "base_url": reporter_config.base_url,
            "service_name": reporter_config.service_name or app.name,
        }
    )
    ControlPlaneReporter(reporter_config).attach(app)


def _build_task_hooks(raw_hooks: Any, *, task_index: int) -> TaskHooks:
    if raw_hooks is None:
        return TaskHooks()
    if not isinstance(raw_hooks, Mapping):
        raise TypeError(f"'tasks[{task_index}].hooks' must be a mapping")
    _validate_unknown_fields(raw_hooks, _STRICT_TASK_HOOK_FIELDS, field=f"tasks[{task_index}].hooks")
    return TaskHooks(
        before=_build_hooks(raw_hooks.get("before"), field=f"tasks[{task_index}].hooks.before"),
        after_success=_build_hooks(
            raw_hooks.get("after_success"),
            field=f"tasks[{task_index}].hooks.after_success",
        ),
        on_failure=_build_hooks(raw_hooks.get("on_failure"), field=f"tasks[{task_index}].hooks.on_failure"),
    )


def _build_hooks(raw_hooks: Any, *, field: str) -> tuple[Callable[..., Any], ...]:
    if raw_hooks is None:
        return ()
    if isinstance(raw_hooks, Sequence) and not isinstance(raw_hooks, (str, bytes)):
        entries = list(raw_hooks)
    else:
        entries = [raw_hooks]
    hooks: list[Callable[..., Any]] = []
    for entry in entries:
        func, _ = _resolve_callable_ref(entry, field=field)
        hooks.append(_bind_callable(func))
    return tuple(hooks)


def _resolve_callable_ref(raw_value: Any, *, field: str) -> tuple[Callable[..., Any], str]:
    if raw_value is None:
        raise ValueError(f"{field} is required")
    if isinstance(raw_value, str):
        ref = raw_value
        params: Mapping[str, Any] = {}
    elif isinstance(raw_value, Mapping):
        _validate_unknown_fields(raw_value, _STRICT_HANDLER_FIELDS, field=field)
        ref = _require_string(raw_value, "ref")
        params = raw_value.get("params", {})
        if not isinstance(params, Mapping):
            raise TypeError(f"'{field}.params' must be a mapping")
    else:
        raise TypeError(f"'{field}' must be a string ref or mapping")

    func = _load_ref(ref)
    if not callable(func):
        raise TypeError(f"{field} ref {ref!r} did not resolve to a callable")
    return _bind_callable(func, params=params), ref


def _bind_callable(func: Callable[..., Any], *, params: Mapping[str, Any] | None = None) -> Callable[..., Any]:
    if not params:
        return func

    bound_params = copy.deepcopy(dict(params))

    @functools.wraps(func)
    async def bound(*args):
        result = func(*args, **copy.deepcopy(bound_params))
        if inspect.isawaitable(result):
            return await result
        return result

    return bound


def _import_yaml():
    try:
        return importlib.import_module("yaml")
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on environment
        raise ModuleNotFoundError(
            "YAML config support requires PyYAML. Install it with: pip install 'onestep[yaml]'"
        ) from exc
