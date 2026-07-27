from __future__ import annotations

import httpx

from onestep import ConnectorErrorKind
from onestep_elasticsearch.resilience import (
    classify_elasticsearch_exception,
    classify_elasticsearch_status,
)


def test_status_classification() -> None:
    assert classify_elasticsearch_status(429) is ConnectorErrorKind.THROTTLED
    assert classify_elasticsearch_status(503) is ConnectorErrorKind.TRANSIENT
    assert classify_elasticsearch_status(401) is ConnectorErrorKind.MISCONFIGURED
    assert classify_elasticsearch_status(400) is ConnectorErrorKind.PERMANENT


def test_http_exception_classification() -> None:
    request = httpx.Request("POST", "https://search/_bulk")
    assert classify_elasticsearch_exception(httpx.ConnectError("down", request=request)) is ConnectorErrorKind.DISCONNECTED
    assert classify_elasticsearch_exception(httpx.ReadTimeout("late", request=request)) is ConnectorErrorKind.UNCERTAIN
