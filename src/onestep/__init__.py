from importlib.metadata import PackageNotFoundError, version as _package_version

from .app import OneStepApp
from .config import load_app_config, load_yaml_app
from .context import TaskContext
from .control_plane_ws import (
    AgentHelloAck,
    AgentProtocolError,
    ControlPlaneWsSender,
    ControlPlaneWsTransport,
    build_control_plane_http_base_url,
    build_control_plane_ws_url,
)
from .envelope import Envelope
from .events import InMemoryMetrics, StructuredEventLogger, TaskEvent, TaskEventKind
from .identity_store import (
    IdentityLockError,
    IdentityStateError,
    IdentityStore,
    build_default_state_dir,
    derive_replica_instance_id,
)
from .reporter import ControlPlaneReporter, ControlPlaneReporterConfig
from .resource_registry import (
    ResourceBuildContext,
    ResourceRegistry,
    ResourceSpecHandler,
    ResourceValidationContext,
    get_resource_handler,
    load_resource_plugins,
    register_resource_type,
)
from .resilience import (
    ConnectorErrorKind,
    ConnectorOperation,
    ConnectorOperationError,
    register_connector_error_classifier,
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

__all__ = [
    "AgentHelloAck",
    "AgentProtocolError",
    "BearerAuth",
    "CronSource",
    "CursorStore",
    "ControlPlaneReporter",
    "ControlPlaneReporterConfig",
    "ControlPlaneWsSender",
    "ControlPlaneWsTransport",
    "ConnectorErrorKind",
    "ConnectorOperation",
    "ConnectorOperationError",
    "Delivery",
    "Envelope",
    "FailureInfo",
    "FailureKind",
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
    "ResourceBuildContext",
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
    "WebhookResponse",
    "WebhookSource",
    "__version__",
    "build_control_plane_http_base_url",
    "build_control_plane_ws_url",
    "build_default_state_dir",
    "derive_replica_instance_id",
    "get_resource_handler",
    "load_app_config",
    "load_resource_plugins",
    "load_yaml_app",
    "register_resource_type",
    "register_connector_error_classifier",
]
