from __future__ import annotations

from onestep.resilience import ConnectorErrorKind, register_connector_error_classifier

try:  # pragma: no cover - optional dependency
    import botocore.exceptions as botocore_exceptions
except ImportError:  # pragma: no cover - optional dependency
    botocore_exceptions = None


def classify_sqs_error(exc: BaseException) -> ConnectorErrorKind | None:
    if isinstance(exc, (ConnectionError, OSError, TimeoutError)):
        return ConnectorErrorKind.DISCONNECTED
    if botocore_exceptions is None:
        return None

    transient_types = []
    for name in ("EndpointConnectionError", "ConnectionClosedError", "ReadTimeoutError", "ConnectTimeoutError"):
        error_type = getattr(botocore_exceptions, name, None)
        if isinstance(error_type, type):
            transient_types.append(error_type)
    if transient_types and isinstance(exc, tuple(transient_types)):
        return ConnectorErrorKind.DISCONNECTED

    client_error = getattr(botocore_exceptions, "ClientError", None)
    if isinstance(client_error, type) and isinstance(exc, client_error):
        code = str(exc.response.get("Error", {}).get("Code", "")).lower()
        if code in {
            "throttling",
            "throttlingexception",
            "requestthrottled",
            "toomanyrequestsexception",
            "slowdown",
        }:
            return ConnectorErrorKind.THROTTLED
        if code in {"requesttimeout", "internalerror", "serviceunavailable"}:
            return ConnectorErrorKind.TRANSIENT
        if code in {
            "accessdenied",
            "accessdeniedexception",
            "invalidclienttokenid",
            "signaturedoesnotmatch",
            "aws.simplequeueservice.nonexistentqueue",
        }:
            return ConnectorErrorKind.MISCONFIGURED
        return ConnectorErrorKind.PERMANENT
    return None


def register_error_classifiers() -> None:
    register_connector_error_classifier("sqs", classify_sqs_error)
