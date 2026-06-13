from __future__ import annotations

import copy
import functools
import importlib
import inspect
import logging
import os
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .app import OneStepApp
from .connectors.base import Sink, Source
from .reporter import ControlPlaneReporter, ControlPlaneReporterConfig
from .resource_registry import (
    ResourceBuildContext,
    ResourceRegistry,
    ResourceValidationContext,
    default_resource_registry,
    is_state_store as _is_state_store,
    load_ref as _load_ref,
    load_resource_plugins,
    mapping_value as _mapping_value,
    normalize_resource_type as _normalize_resource_type,
    optional_string as _optional_string,
    require_string as _require_string,
    string_list as _string_list,
    string_value as _string_value,
    validate_unknown_fields as _validate_unknown_fields,
)
from .retry import MaxAttempts, NoRetry, RetryPolicy
from .task import TaskHooks

_YAML_SUFFIXES = (".yaml", ".yml")

# Pattern for ${VAR} or ${VAR:-default} or ${VAR:default}
_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")

# Pattern for .env file lines: KEY=VALUE with optional quoting and trailing comment
_DOTENV_LINE_RE = re.compile(
    r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*'   # KEY=
    r'(?:"([^"]*)"|'                           # "value"
    r"'([^']*)'|"                               # 'value'
    r'([^\s#][^#]*))'                           # value
    r'\s*(?:\#.*)?$'                           # optional trailing comment
)

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
_STRICT_APP_FIELDS = frozenset({"name", "shutdown_timeout_s", "config", "state", "logging", "env_file", "strict_env"})
_STRICT_APP_LOGGING_FIELDS = frozenset({"level"})
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


def _load_dotenv(path: str) -> int:
    """Parse a .env file and inject key=value pairs into os.environ.

    Uses os.environ.setdefault so existing environment variables take
    precedence over those defined in the .env file.

    Returns the number of keys loaded from the file.
    """
    logger = logging.getLogger("onestep")
    loaded = 0
    with open(path, "r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            line = line.rstrip("\n\r")
            stripped = line.strip()
            # Skip empty lines and whole-line comments
            if not stripped or stripped.startswith("#"):
                continue
            match = _DOTENV_LINE_RE.match(line)
            if not match:
                logger.warning(
                    "skipped malformed .env line %d in %s: %r",
                    lineno,
                    path,
                    stripped,
                )
                continue
            key = match.group(1)
            if match.group(2) is not None:
                value = match.group(2)
            elif match.group(3) is not None:
                value = match.group(3)
            else:
                value = (match.group(4) or "").strip()
            previous = os.environ.get(key)
            if previous is not None:
                logger.debug("skipped %s from %s (already set in environment)", key, path)
            else:
                os.environ[key] = value
                loaded += 1
    return loaded


def _collect_env_refs(config: Any) -> dict[str, list[str]]:
    """Collect all ${VAR} references without defaults that are missing from the environment.

    Returns a dict mapping missing variable names to their field paths.
    """
    ref_re = re.compile(r"\$\{([^}]+)\}")
    missing: dict[str, list[str]] = {}

    def _walk(value: Any, field_path: str) -> None:
        if isinstance(value, str):
            for match in ref_re.finditer(value):
                content = match.group(1)
                # ${VAR:-default} or ${VAR:default} → skip (has default)
                if ":-" in content or ":" in content:
                    continue
                var_name = content.strip()
                if var_name and var_name not in os.environ:
                    missing.setdefault(var_name, []).append(field_path)
        elif isinstance(value, Mapping):
            for k, v in value.items():
                _walk(v, f"{field_path}.{k}")
        elif isinstance(value, list):
            for i, v in enumerate(value):
                _walk(v, f"{field_path}[{i}]")

    _walk(config, "config")
    return missing


def is_yaml_target(target: str) -> bool:
    return target.lower().endswith(_YAML_SUFFIXES)


def load_yaml_app(
    path: str,
    *,
    strict: bool = False,
    env_file: str | None = None,
    strict_env: bool | None = None,
) -> OneStepApp:
    logger = logging.getLogger("onestep")
    yaml = _import_yaml()
    resolved_path = os.path.abspath(path)
    with open(resolved_path, "r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, Mapping):
        raise TypeError("YAML app config must be a mapping at the top level")

    # --- Determine env_file path ---
    # Priority: CLI arg > app.env_file > auto-detect YAML-adjacent .env
    dotenv_path: str | None = None
    if env_file is not None:
        dotenv_path = env_file
    elif "app" in loaded and isinstance(loaded.get("app"), Mapping):
        app_env_file = loaded["app"].get("env_file")
        if app_env_file is not None:
            if not isinstance(app_env_file, str):
                raise TypeError("'app.env_file' must be a string")
            dotenv_path = app_env_file
    if dotenv_path is not None:
        if not os.path.isabs(dotenv_path):
            dotenv_path = os.path.join(os.path.dirname(resolved_path), dotenv_path)
        if not os.path.isfile(dotenv_path):
            raise ValueError(f"env_file not found: {dotenv_path}")

    # Auto-detect: try YAML-adjacent .env (silently skip if missing)
    if dotenv_path is None:
        candidate = os.path.join(os.path.dirname(resolved_path), ".env")
        if os.path.isfile(candidate):
            dotenv_path = candidate
            logger.debug("auto-detected .env next to %s", resolved_path)
        else:
            logger.debug("no .env found next to %s", resolved_path)

    if dotenv_path is not None:
        count = _load_dotenv(dotenv_path)
        if count > 0:
            logger.debug("loaded %d vars from %s", count, dotenv_path)

    # --- Determine strict_env flag ---
    # Priority: CLI arg > app.strict_env > False
    effective_strict_env = False
    if strict_env is not None:
        effective_strict_env = strict_env
    elif "app" in loaded and isinstance(loaded.get("app"), Mapping):
        app_strict_env = loaded["app"].get("strict_env")
        if isinstance(app_strict_env, bool):
            effective_strict_env = app_strict_env
        elif app_strict_env is not None:
            raise TypeError("'app.strict_env' must be a boolean")

    if effective_strict_env:
        missing = _collect_env_refs(loaded)
        if missing:
            parts = ["strict_env: missing required environment variable(s):"]
            for var_name in sorted(missing):
                for field_path in missing[var_name]:
                    parts.append(f"  - {var_name} (referenced at: {field_path})")
            parts.append(
                "Hint: define them in the shell, or add them to your .env file, "
                "or provide a default with ${VAR:-default_value}."
            )
            raise ValueError("\n".join(parts))

    # Expand environment variables in the loaded config
    expanded = _expand_env_vars(loaded)
    return load_app_config(expanded, source_path=resolved_path, strict=strict)


def load_app_config(
    config: Mapping[str, Any],
    *,
    source_path: str | None = None,
    strict: bool = False,
) -> OneStepApp:
    resource_registry = _ensure_resource_registry_loaded()
    if strict:
        validate_app_config(config, registry=resource_registry)
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
    _apply_app_logging(app, app_section.get("logging") if app_section is not None else None)

    resources = _build_resources(config, registry=resource_registry)
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
        source = _resolve_optional_source(resources, task_config.get("source"), task_index=index)
        emit = _resolve_optional_sinks(resources, task_config.get("emit"), field="emit", task_index=index)
        dead_letter = _resolve_optional_sinks(
            resources,
            task_config.get("dead_letter"),
            field="dead_letter",
            task_index=index,
        )
        handler, handler_ref = _build_task_handler(
            task_config.get("handler"),
            has_handler="handler" in task_config,
            has_emit=bool(emit),
            task_name=task_name,
            task_index=index,
        )
        app.task(
            name=task_name,
            description=_optional_string(task_config, "description"),
            source=source,
            emit=emit,
            dead_letter=dead_letter,
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


def _ensure_resource_registry_loaded() -> ResourceRegistry:
    from .resources import register_builtin_resources

    registry = default_resource_registry()
    register_builtin_resources(registry)
    load_resource_plugins(registry)
    return registry


def _build_resources(config: Mapping[str, Any], *, registry: ResourceRegistry) -> dict[str, Any]:
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
            built = _build_resource(name, spec, resolve=resolve, registry=registry)
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


def _build_resource(
    name: str,
    spec: Mapping[str, Any],
    *,
    resolve: Callable[[str], Any],
    registry: ResourceRegistry,
) -> Any:
    resource_type = _normalize_resource_type(_require_string(spec, "type"))
    handler = registry.get_resource_handler(resource_type)
    if handler is None:
        raise ValueError(f"unsupported resource type {resource_type!r} for resource {name!r}")
    context = ResourceBuildContext(
        name=name,
        type=resource_type,
        field=f"resources.{name}",
        _resolve=resolve,
    )
    return handler.build(context, spec)


def _build_task_handler(
    raw_handler: Any,
    *,
    has_handler: bool,
    has_emit: bool,
    task_name: str | None,
    task_index: int,
):
    if has_handler:
        return _build_handler(raw_handler, task_name=task_name)
    if not has_emit:
        raise ValueError(f"tasks[{task_index}] must define either 'handler' or 'emit'")
    return _build_passthrough_handler(task_name), None


def _build_passthrough_handler(task_name: str | None):
    async def passthrough(ctx, payload):
        return payload

    passthrough.__name__ = task_name or "passthrough"
    return passthrough


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


def validate_app_config(config: Mapping[str, Any], *, registry: ResourceRegistry | None = None) -> None:
    resource_registry = registry or _ensure_resource_registry_loaded()
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
        _validate_app_logging(app_section.get("logging"))
        _validate_app_env_file(app_section.get("env_file"))
        _validate_app_strict_env(app_section.get("strict_env"))
        legacy_fields = sorted(field for field in _LEGACY_APP_FIELDS if field in config)
        if legacy_fields:
            raise ValueError(
                "strict mode does not allow mixing 'app' with legacy top-level app fields: "
                + ", ".join(legacy_fields)
            )

    _validate_reporter_config(config.get("reporter"))
    _validate_resource_sections(config, registry=resource_registry)
    _validate_hooks_config(config.get("hooks"), field="hooks", allowed=_STRICT_APP_HOOK_FIELDS)
    _validate_tasks(config.get("tasks"))


def _validate_resource_sections(config: Mapping[str, Any], *, registry: ResourceRegistry) -> None:
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
            handler = registry.get_resource_handler(normalized_type)
            if handler is None:
                raise ValueError(f"unsupported resource type {normalized_type!r} for {field}")
            if handler.allowed_fields is not None:
                _validate_unknown_fields(raw_spec, handler.allowed_fields, field=field)
            if handler.validate is not None:
                context = ResourceValidationContext(name=str(name), type=normalized_type, field=field)
                handler.validate(context, raw_spec)


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
        if "handler" in raw_task:
            _validate_ref_entry(raw_task.get("handler"), field=f"{field}.handler")
        elif not _task_emit_configured(raw_task.get("emit")):
            raise ValueError(f"{field} must define either 'handler' or 'emit'")
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


def _validate_app_logging(raw_logging: Any) -> None:
    if raw_logging is None:
        return
    if not isinstance(raw_logging, Mapping):
        raise TypeError("'app.logging' must be a mapping")
    _validate_unknown_fields(raw_logging, _STRICT_APP_LOGGING_FIELDS, field="app.logging")
    level = _require_string(raw_logging, "level")
    resolved = getattr(logging, level.strip().upper(), None)
    if not isinstance(resolved, int):
        raise ValueError(f"unsupported logging level {level!r}")


def _validate_app_env_file(raw_env_file: Any) -> None:
    if raw_env_file is None:
        return
    if not isinstance(raw_env_file, str):
        raise TypeError("'app.env_file' must be a string")


def _validate_app_strict_env(raw_strict_env: Any) -> None:
    if raw_strict_env is None:
        return
    if not isinstance(raw_strict_env, bool):
        raise TypeError("'app.strict_env' must be a boolean")


def _apply_app_logging(app: OneStepApp, raw_logging: Any) -> None:
    if raw_logging is None:
        return
    if not isinstance(raw_logging, Mapping):
        raise TypeError("'app.logging' must be a mapping")
    _validate_unknown_fields(raw_logging, _STRICT_APP_LOGGING_FIELDS, field="app.logging")
    level = _require_string(raw_logging, "level")
    resolved = getattr(logging, level.strip().upper(), None)
    if not isinstance(resolved, int):
        raise ValueError(f"unsupported logging level {level!r}")
    logging.getLogger("onestep").setLevel(resolved)


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


def _task_emit_configured(raw_emit: Any) -> bool:
    if raw_emit is None:
        return False
    if isinstance(raw_emit, Sequence) and not isinstance(raw_emit, (str, bytes)):
        return bool(raw_emit)
    return True


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
