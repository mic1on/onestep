from importlib.metadata import PackageNotFoundError, version as _package_version

from .app import OneStepApp
from .config import load_app_config, load_yaml_app
from .context import TaskContext
from .envelope import Envelope
from .events import InMemoryMetrics, StructuredEventLogger, TaskEvent, TaskEventKind
from .reporter import ControlPlaneReporter, ControlPlaneReporterConfig
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
from .state_sqlalchemy import SQLAlchemyCursorStore, SQLAlchemyStateStore
from .connectors.base import Delivery, Sink, Source
from .connectors.memory import MemoryQueue
from .connectors.mysql import MySQLConnector
from .connectors.rabbitmq import RabbitMQConnector
from .connectors.redis import RedisConnector
from .connectors.schedule import CronSource, IntervalSource
from .connectors.sqs import SQSConnector
from .connectors.webhook import BearerAuth, WebhookResponse, WebhookSource

try:
    __version__ = _package_version("onestep")
except PackageNotFoundError:  # pragma: no cover - local source tree before install
    __version__ = "dev"

__all__ = [
    "BearerAuth",
    "CronSource",
    "CursorStore",
    "ControlPlaneReporter",
    "ControlPlaneReporterConfig",
    "ConnectorErrorKind",
    "ConnectorOperation",
    "ConnectorOperationError",
    "Delivery",
    "Envelope",
    "FailureInfo",
    "FailureKind",
    "InMemoryMetrics",
    "InMemoryCursorStore",
    "InMemoryStateStore",
    "IntervalSource",
    "MaxAttempts",
    "MemoryQueue",
    "MySQLConnector",
    "NoRetry",
    "OneStepApp",
    "RabbitMQConnector",
    "RedisConnector",
    "RetryAction",
    "RetryDecision",
    "RetryPolicy",
    "SQLAlchemyCursorStore",
    "SQLAlchemyStateStore",
    "Sink",
    "StateStore",
    "StructuredEventLogger",
    "Source",
    "SQSConnector",
    "ScopedState",
    "TaskEvent",
    "TaskEventKind",
    "TaskContext",
    "WebhookResponse",
    "WebhookSource",
    "__version__",
    "load_app_config",
    "load_yaml_app",
]
