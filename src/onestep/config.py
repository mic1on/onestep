from __future__ import annotations

import copy
import importlib
import inspect
import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .app import OneStepApp
from .connectors.base import Sink, Source
from .connectors.memory import MemoryQueue
from .connectors.mysql import MySQLConnector
from .connectors.rabbitmq import RabbitMQConnector
from .connectors.schedule import CronSource, IntervalSource
from .connectors.webhook import BearerAuth, WebhookResponse, WebhookSource
from .retry import MaxAttempts, NoRetry, RetryPolicy

_YAML_SUFFIXES = (".yaml", ".yml")


def is_yaml_target(target: str) -> bool:
    return target.lower().endswith(_YAML_SUFFIXES)


def load_yaml_app(path: str) -> OneStepApp:
    yaml = _import_yaml()
    resolved_path = os.path.abspath(path)
    with open(resolved_path, "r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, Mapping):
        raise TypeError("YAML app config must be a mapping at the top level")
    return load_app_config(loaded, source_path=resolved_path)


def load_app_config(config: Mapping[str, Any], *, source_path: str | None = None) -> OneStepApp:
    app_section = config.get("app")
    if app_section is None:
        app_name = _require_string(config, "name")
        shutdown_timeout_s = config.get("shutdown_timeout_s", 30.0)
        app_config = config.get("config")
    else:
        if not isinstance(app_section, Mapping):
            raise TypeError("'app' must be a mapping")
        app_name = _require_string(app_section, "name")
        shutdown_timeout_s = app_section.get("shutdown_timeout_s", 30.0)
        app_config = app_section.get("config")

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
    tasks = config.get("tasks", [])
    if not isinstance(tasks, Sequence) or isinstance(tasks, (str, bytes)):
        raise TypeError("'tasks' must be a list")

    for index, task_config in enumerate(tasks):
        if not isinstance(task_config, Mapping):
            raise TypeError(f"'tasks[{index}]' must be a mapping")
        task_name = _optional_string(task_config, "name")
        handler = _build_handler(task_config.get("handler"), task_name=task_name)
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
            concurrency=task_config.get("concurrency", 1),
            retry=_build_retry(task_config.get("retry")),
            timeout_s=task_config.get("timeout_s"),
        )(handler)

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
    for section_name in ("connectors", "sources", "sinks"):
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
    resource_type = _require_string(spec, "type").strip().lower().replace("-", "_").replace(".", "_")
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
    if raw_handler is None:
        raise ValueError("task handler is required")
    if isinstance(raw_handler, str):
        ref = raw_handler
        params: Mapping[str, Any] = {}
    elif isinstance(raw_handler, Mapping):
        ref = _require_string(raw_handler, "ref")
        params = raw_handler.get("params", {})
        if not isinstance(params, Mapping):
            raise TypeError("'handler.params' must be a mapping")
    else:
        raise TypeError("'handler' must be a string ref or mapping")

    func = _load_ref(ref)
    if not callable(func):
        raise TypeError(f"handler ref {ref!r} did not resolve to a callable")
    if not params:
        return func

    bound_params = copy.deepcopy(dict(params))

    async def bound_handler(ctx, payload):
        result = func(ctx, payload, **copy.deepcopy(bound_params))
        if inspect.isawaitable(result):
            return await result
        return result

    bound_handler.__name__ = task_name or getattr(func, "__name__", "configured_handler")
    bound_handler.__doc__ = getattr(func, "__doc__", None)
    return bound_handler


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

    normalized = retry_type.strip().lower().replace("-", "_")
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


def _is_cursor_store(value: Any) -> bool:
    return hasattr(value, "load") and hasattr(value, "save")


def _import_yaml():
    try:
        return importlib.import_module("yaml")
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on environment
        raise ModuleNotFoundError(
            "YAML config support requires PyYAML. Install it with: pip install 'onestep[yaml]'"
        ) from exc
