from importlib.metadata import PackageNotFoundError, version as _package_version
from importlib.util import find_spec as _find_spec

from .app import OneStepApp
from .config import load_app_config, load_resource_catalog, load_yaml_app
from .context import TaskContext
from .envelope import Envelope
from .events import InMemoryMetrics, StructuredEventLogger, TaskEvent, TaskEventKind
from .identity_store import (
    IdentityLockError,
    IdentityStateError,
    IdentityStore,
    build_default_state_dir,
    derive_replica_instance_id,
)
from .metrics import CounterMetric, CustomMetricsRegistry, GaugeMetric, TaskMetrics
from .resource_registry import (
    CATALOG_FIELD_TYPES,
    CATALOG_ROLES,
    ResourceCatalogEntry,
    ResourceCatalogField,
    ResourceBuildContext,
    ResourceRegistry,
    ResourceSpecHandler,
    ResourceValidationContext,
    get_resource_catalog_entry,
    get_resource_handler,
    load_resource_plugins,
    register_resource_type,
)
from .resilience import (
    ConnectorErrorKind,
    ConnectorOperation,
    ConnectorOperationError,
)
from .retry import (
    FailureInfo,
    FailureKind,
    MaxAttempts,
    NoRetry,
    RetryAction,
    RetryDecision,
    RetryPolicy,
)
from .state import CursorStore, InMemoryCursorStore, InMemoryStateStore, ScopedState, StateStore
from .connectors.base import Delivery, Sink, Source
from .connectors.http import HttpSink, HttpSinkStatusError
from .connectors.memory import MemoryQueue
from .connectors.schedule import CronSource, IntervalSource
from .connectors.webhook import BearerAuth, WebhookResponse, WebhookSource

try:
    __version__ = _package_version("onestep")
except PackageNotFoundError:  # pragma: no cover - local source tree before install
    __version__ = "dev"

_CONTROL_PLANE_EXPORTS = {
    "AgentHelloAck",
    "AgentProtocolError",
    "ControlPlaneReporter",
    "ControlPlaneReporterConfig",
    "ControlPlaneWsSender",
    "ControlPlaneWsTransport",
    "build_control_plane_http_base_url",
    "build_control_plane_ws_url",
}


def __getattr__(name: str):
    if name in _CONTROL_PLANE_EXPORTS:
        try:
            import onestep_control_plane
        except ImportError as exc:
            from .reporter_registry import missing_control_plane_plugin_error

            raise missing_control_plane_plugin_error() from exc
        return getattr(onestep_control_plane, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_CORE_EXPORTS = [
    "BearerAuth",
    "CronSource",
    "CursorStore",
    "ConnectorErrorKind",
    "ConnectorOperation",
    "ConnectorOperationError",
    "CounterMetric",
    "CustomMetricsRegistry",
    "Delivery",
    "Envelope",
    "FailureInfo",
    "FailureKind",
    "GaugeMetric",
    "HttpSink",
    "HttpSinkStatusError",
    "IdentityLockError",
    "IdentityStateError",
    "IdentityStore",
    "InMemoryMetrics",
    "InMemoryCursorStore",
    "InMemoryStateStore",
    "IntervalSource",
    "MaxAttempts",
    "MemoryQueue",
    "NoRetry",
    "OneStepApp",
    "CATALOG_FIELD_TYPES",
    "CATALOG_ROLES",
    "ResourceBuildContext",
    "ResourceCatalogEntry",
    "ResourceCatalogField",
    "ResourceRegistry",
    "ResourceSpecHandler",
    "ResourceValidationContext",
    "RetryAction",
    "RetryDecision",
    "RetryPolicy",
    "Sink",
    "StateStore",
    "StructuredEventLogger",
    "Source",
    "ScopedState",
    "TaskEvent",
    "TaskEventKind",
    "TaskContext",
    "TaskMetrics",
    "WebhookResponse",
    "WebhookSource",
    "__version__",
    "build_default_state_dir",
    "derive_replica_instance_id",
    "get_resource_catalog_entry",
    "get_resource_handler",
    "load_app_config",
    "load_resource_catalog",
    "load_resource_plugins",
    "load_yaml_app",
    "register_resource_type",
]

__all__ = list(_CORE_EXPORTS)
if _find_spec("onestep_control_plane") is not None:
    __all__.extend(sorted(_CONTROL_PLANE_EXPORTS))
