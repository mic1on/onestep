from __future__ import annotations

from typing import Any

from onestep.resilience import ConnectorErrorKind, register_connector_error_classifier

try:  # pragma: no cover - optional dependency
    import aio_pika.exceptions as aio_pika_exceptions
except ImportError:  # pragma: no cover - optional dependency
    aio_pika_exceptions = None

try:  # pragma: no cover - optional dependency
    import aiormq.exceptions as aiormq_exceptions
except ImportError:  # pragma: no cover - optional dependency
    aiormq_exceptions = None


def classify_rabbitmq_error(exc: BaseException) -> ConnectorErrorKind | None:
    if isinstance(exc, (ConnectionError, OSError)):
        return ConnectorErrorKind.DISCONNECTED
    if isinstance(exc, RuntimeError):
        message = str(exc).lower()
        if "closed" in message or "no active transport" in message:
            return ConnectorErrorKind.DISCONNECTED

    if aio_pika_exceptions is not None:
        result = _classify_amqp_error(exc, aio_pika_exceptions)
        if result is not None:
            return result
    if aiormq_exceptions is not None:
        result = _classify_amqp_error(exc, aiormq_exceptions)
        if result is not None:
            return result
    return None


def _classify_amqp_error(exc: BaseException, module: Any) -> ConnectorErrorKind | None:
    disconnected_names = {
        "AMQPConnectionError",
        "AMQPChannelError",
        "ConnectionClosed",
        "ChannelClosed",
        "ChannelInvalidStateError",
        "DeliveryError",
    }
    misconfigured_names = {
        "AuthenticationError",
        "ProbableAuthenticationError",
        "IncompatibleProtocolError",
    }
    permanent_names = {
        "ChannelNotFoundEntity",
        "ChannelPreconditionFailed",
    }
    for name in disconnected_names:
        error_type = getattr(module, name, None)
        if isinstance(error_type, type) and isinstance(exc, error_type):
            return ConnectorErrorKind.DISCONNECTED
    for name in misconfigured_names:
        error_type = getattr(module, name, None)
        if isinstance(error_type, type) and isinstance(exc, error_type):
            return ConnectorErrorKind.MISCONFIGURED
    for name in permanent_names:
        error_type = getattr(module, name, None)
        if isinstance(error_type, type) and isinstance(exc, error_type):
            return ConnectorErrorKind.PERMANENT
    return None


def register_error_classifiers() -> None:
    register_connector_error_classifier("rabbitmq", classify_rabbitmq_error)
