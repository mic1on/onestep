from __future__ import annotations

import ssl

import httpx
from onestep_elasticsearch.resilience import (
    classify_elasticsearch_exception,
    classify_elasticsearch_status,
)

from onestep import ConnectorErrorKind


def test_status_classification() -> None:
    assert classify_elasticsearch_status(429) is ConnectorErrorKind.THROTTLED
    assert classify_elasticsearch_status(503) is ConnectorErrorKind.TRANSIENT
    assert classify_elasticsearch_status(401) is ConnectorErrorKind.MISCONFIGURED
    assert classify_elasticsearch_status(400) is ConnectorErrorKind.PERMANENT


def test_http_exception_classification() -> None:
    request = httpx.Request("POST", "https://search/_bulk")
    assert (
        classify_elasticsearch_exception(httpx.ConnectError("down", request=request))
        is ConnectorErrorKind.DISCONNECTED
    )
    assert (
        classify_elasticsearch_exception(httpx.ReadTimeout("late", request=request))
        is ConnectorErrorKind.UNCERTAIN
    )
    assert (
        classify_elasticsearch_exception(
            httpx.ConnectTimeout("not submitted", request=request)
        )
        is ConnectorErrorKind.DISCONNECTED
    )


def test_tls_verification_failure_is_misconfigured() -> None:
    request = httpx.Request("GET", "https://search/")
    exc = httpx.ConnectError("TLS failed", request=request)
    exc.__cause__ = ssl.SSLCertVerificationError("certificate verify failed")

    assert classify_elasticsearch_exception(exc) is ConnectorErrorKind.MISCONFIGURED
