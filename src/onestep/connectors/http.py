from __future__ import annotations

import asyncio
import json
import urllib.parse
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from onestep.envelope import Envelope
from onestep.resilience import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError

from .base import Sink

_DEFAULT_SUCCESS_STATUSES = (200, 201, 202, 204)
_DEFAULT_TIMEOUT_S = 5.0
_BODYLESS_METHODS = {"DELETE", "GET"}
_REDACTED = "<redacted>"


class HttpSinkStatusError(RuntimeError):
    def __init__(self, *, name: str, status: int, reason: str, body: bytes) -> None:
        self.name = name
        self.status = status
        self.reason = reason
        self.body = body
        super().__init__(f"http_sink {name!r} returned HTTP {status} {reason}".rstrip())


class HttpSink(Sink):
    def __init__(
        self,
        name: str,
        *,
        url: str,
        method: str = "POST",
        headers: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        success_statuses: Sequence[int] | None = None,
    ) -> None:
        super().__init__(name)
        self.url = _normalize_url(url)
        self.method = _normalize_method(method)
        self.headers = _normalize_headers(headers)
        self.params = _normalize_params(params, field="params")
        self.timeout_s = _normalize_timeout(timeout_s)
        self.success_statuses = _normalize_success_statuses(success_statuses)

    async def send(self, envelope: Envelope) -> None:
        request_url = self._request_url(envelope)
        payload = self._request_payload(envelope)
        headers = dict(self.headers)
        if payload is not None:
            _set_header_default(headers, "Content-Type", "application/json")
        request = urllib.request.Request(
            request_url,
            data=payload,
            headers=headers,
            method=self.method,
        )
        try:
            status, reason, body = await asyncio.to_thread(self._send_request, request)
        except ConnectorOperationError:
            raise
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise ConnectorOperationError(
                backend="http_sink",
                operation=ConnectorOperation.SEND,
                kind=_classify_transport_error(exc),
                source_name=self.name,
                retry_delay_s=self.timeout_s,
                cause=exc,
            ) from exc

        if status not in self.success_statuses:
            status_error = HttpSinkStatusError(
                name=self.name,
                status=status,
                reason=reason,
                body=body,
            )
            raise ConnectorOperationError(
                backend="http_sink",
                operation=ConnectorOperation.SEND,
                kind=_classify_status(status),
                source_name=self.name,
                cause=status_error,
                message=f"http_sink send failed for {self.name!r}: HTTP {status} {reason}".rstrip(),
            ) from status_error

    def control_plane_descriptor(self) -> dict[str, Any]:
        return {
            "kind": "http_sink",
            "name": self.name,
            "config": {
                "url": _redact_url(self.url),
                "method": self.method,
                "headers": {key: _REDACTED for key in sorted(self.headers)},
                "params": {key: _REDACTED for key in sorted(self.params)},
                "timeout_s": self.timeout_s,
                "success_statuses": list(self.success_statuses),
            },
        }

    def _request_payload(self, envelope: Envelope) -> bytes | None:
        if self.method in _BODYLESS_METHODS:
            return None
        return json.dumps(envelope.body, default=str).encode("utf-8")

    def _request_url(self, envelope: Envelope) -> str:
        params: dict[str, Any] = dict(self.params)
        if self.method in _BODYLESS_METHODS and envelope.body is not None:
            params.update(_normalize_params(envelope.body, field="envelope.body"))
        return _append_query_params(self.url, params)

    def _send_request(self, request: urllib.request.Request) -> tuple[int, str, bytes]:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                return response.status, response.reason, response.read()
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read()
            finally:
                exc.close()
            reason = str(getattr(exc, "reason", None) or getattr(exc, "msg", ""))
            return exc.code, reason, body


def _normalize_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("'url' must be a non-empty string")
    normalized = value.strip()
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("'url' must be an http or https URL")
    return normalized


def _normalize_method(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("'method' must be a non-empty string")
    return value.strip().upper()


def _normalize_headers(value: Mapping[str, Any] | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("'headers' must be a mapping")
    headers: dict[str, str] = {}
    for key, item in value.items():
        name = str(key).strip()
        if not name:
            raise ValueError("header names must be non-empty")
        headers[name] = str(item)
    return headers


def _normalize_params(value: Mapping[str, Any] | None, *, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"'{field}' must be a mapping")
    params: dict[str, Any] = {}
    for key, item in value.items():
        name = str(key).strip()
        if not name:
            raise ValueError("parameter names must be non-empty")
        params[name] = item
    return params


def _append_query_params(url: str, params: Mapping[str, Any]) -> str:
    if not params:
        return url
    parsed = urlsplit(url)
    query = parsed.query
    encoded = urllib.parse.urlencode(params, doseq=True)
    if query:
        query = f"{query}&{encoded}"
    else:
        query = encoded
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))


def _normalize_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("'timeout_s' must be a number")
    normalized = float(value)
    if normalized <= 0:
        raise ValueError("'timeout_s' must be > 0")
    return normalized


def _normalize_success_statuses(value: Sequence[int] | None) -> tuple[int, ...]:
    if value is None:
        return _DEFAULT_SUCCESS_STATUSES
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError("'success_statuses' must be a list of integers")
    statuses: list[int] = []
    for status in value:
        if isinstance(status, bool) or not isinstance(status, int):
            raise TypeError("'success_statuses' must be a list of integers")
        if status < 100 or status > 599:
            raise ValueError("'success_statuses' must contain HTTP status codes")
        statuses.append(status)
    if not statuses:
        raise ValueError("'success_statuses' must not be empty")
    return tuple(dict.fromkeys(statuses))


def _set_header_default(headers: dict[str, str], name: str, value: str) -> None:
    if any(key.lower() == name.lower() for key in headers):
        return
    headers[name] = value


def _classify_status(status: int) -> ConnectorErrorKind:
    if status == 429:
        return ConnectorErrorKind.THROTTLED
    if status in {408, 425} or status >= 500:
        return ConnectorErrorKind.TRANSIENT
    return ConnectorErrorKind.PERMANENT


def _classify_transport_error(exc: BaseException) -> ConnectorErrorKind:
    if isinstance(exc, TimeoutError):
        return ConnectorErrorKind.DISCONNECTED
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (TimeoutError, OSError)):
            return ConnectorErrorKind.DISCONNECTED
    if isinstance(exc, OSError):
        return ConnectorErrorKind.DISCONNECTED
    return ConnectorErrorKind.TRANSIENT


def _redact_url(value: str) -> str:
    parsed = urlsplit(value)
    netloc = parsed.netloc
    if "@" in netloc:
        _, host = netloc.rsplit("@", 1)
        netloc = f"{_REDACTED}@{host}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
