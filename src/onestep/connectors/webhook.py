from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
from asyncio import Queue, QueueEmpty
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit

from onestep.envelope import Envelope

from .base import Delivery, Source

_DEFAULT_HEADER_TIMEOUT_S = 5.0
_DEFAULT_RESPONSE_BODY = {"accepted": True}
_SUPPORTED_PARSERS = {"auto", "json", "form", "text", "raw"}
_STATUS_TEXT = {
    200: "OK",
    401: "Unauthorized",
    202: "Accepted",
    400: "Bad Request",
    404: "Not Found",
    405: "Method Not Allowed",
    408: "Request Timeout",
    411: "Length Required",
    413: "Payload Too Large",
    500: "Internal Server Error",
    501: "Not Implemented",
    503: "Service Unavailable",
}

_SERVERS: dict[tuple[str, int], "_WebhookServer"] = {}
_SERVERS_LOCK: asyncio.Lock | None = None
_SERVERS_LOOP: asyncio.AbstractEventLoop | None = None


def _server_registry_lock() -> asyncio.Lock:
    global _SERVERS_LOCK, _SERVERS_LOOP
    loop = asyncio.get_running_loop()
    if _SERVERS_LOCK is None or _SERVERS_LOOP is not loop:
        _SERVERS_LOCK = asyncio.Lock()
        _SERVERS_LOOP = loop
        _SERVERS.clear()
    return _SERVERS_LOCK


@dataclass(frozen=True)
class WebhookResponse:
    status_code: int = 202
    body: Any = field(default_factory=lambda: dict(_DEFAULT_RESPONSE_BODY))
    headers: dict[str, str] = field(default_factory=dict)

    def render(self) -> tuple[int, dict[str, str], bytes]:
        headers = {str(key): str(value) for key, value in self.headers.items()}
        body = self.body
        if body is None:
            payload = b""
        elif isinstance(body, bytes):
            payload = body
            headers.setdefault("Content-Type", "application/octet-stream")
        elif isinstance(body, str):
            payload = body.encode("utf-8")
            headers.setdefault("Content-Type", "text/plain; charset=utf-8")
        else:
            payload = json.dumps(body).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        headers["Content-Length"] = str(len(payload))
        headers.setdefault("Connection", "close")
        return self.status_code, headers, payload


@dataclass(frozen=True)
class BearerAuth:
    token: str
    header: str = "authorization"
    scheme: str = "Bearer"
    realm: str = "webhook"

    def __post_init__(self) -> None:
        if not self.token:
            raise ValueError("token must not be empty")
        if not self.header:
            raise ValueError("header must not be empty")
        if not self.scheme:
            raise ValueError("scheme must not be empty")

    def authorize(self, headers: Mapping[str, str]) -> None:
        presented = headers.get(self.header.lower())
        if not presented:
            raise _HTTPError(401, {"error": "unauthorized"}, headers=self.challenge_headers())
        scheme, _, token = presented.partition(" ")
        if not token or scheme.lower() != self.scheme.lower():
            raise _HTTPError(401, {"error": "unauthorized"}, headers=self.challenge_headers())
        if not secrets.compare_digest(token, self.token):
            raise _HTTPError(401, {"error": "unauthorized"}, headers=self.challenge_headers())

    def challenge_headers(self) -> dict[str, str]:
        return {
            "WWW-Authenticate": f'{self.scheme} realm="{self.realm}"',
        }


class WebhookDelivery(Delivery):
    def __init__(self, source: "WebhookSource", envelope: Envelope) -> None:
        super().__init__(envelope)
        self._source = source

    async def ack(self) -> None:
        return None

    async def retry(self, *, delay_s: float | None = None) -> None:
        if delay_s:
            await asyncio.sleep(delay_s)
        await self._source._enqueue(
            Envelope(
                body=self.envelope.body,
                meta=dict(self.envelope.meta),
                attempts=self.envelope.attempts + 1,
            )
        )

    async def fail(self, exc: Exception | None = None) -> None:
        return None


class WebhookSource(Source):
    def __init__(
        self,
        *,
        path: str,
        methods: Sequence[str] = ("POST",),
        host: str = "127.0.0.1",
        port: int = 8080,
        parser: str = "auto",
        auth: BearerAuth | None = None,
        response: WebhookResponse | None = None,
        max_body_bytes: int = 1024 * 1024,
        read_timeout_s: float = 5.0,
        queue_maxsize: int = 1000,
        batch_size: int = 100,
        poll_interval_s: float = 0.1,
        name: str | None = None,
    ) -> None:
        normalized_path = _normalize_path(path)
        if parser not in _SUPPORTED_PARSERS:
            raise ValueError(f"parser must be one of: {', '.join(sorted(_SUPPORTED_PARSERS))}")
        if not methods:
            raise ValueError("methods must contain at least one HTTP method")
        if max_body_bytes < 0:
            raise ValueError("max_body_bytes must be >= 0")
        if read_timeout_s <= 0:
            raise ValueError("read_timeout_s must be > 0")
        if queue_maxsize < 1:
            raise ValueError("queue_maxsize must be >= 1")

        super().__init__(name or f"webhook:{normalized_path}")
        self.path = normalized_path
        self.methods = tuple(dict.fromkeys(method.upper() for method in methods))
        self.host = host
        self.port = port
        self.parser = parser
        self.auth = auth
        self.response = response or WebhookResponse()
        self.max_body_bytes = max_body_bytes
        self.read_timeout_s = read_timeout_s
        self.queue_maxsize = queue_maxsize
        self.batch_size = batch_size
        self.poll_interval_s = poll_interval_s
        self._queue: Queue | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: _WebhookServer | None = None

    @property
    def bound_port(self) -> int:
        if self._server is None:
            return self.port
        return self._server.bound_port

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.bound_port}{self.path}"

    async def open(self) -> None:
        self._ensure_queue()
        lock = _server_registry_lock()
        async with lock:
            key = (self.host, self.port)
            server = _SERVERS.get(key)
            if server is None:
                server = _WebhookServer(self.host, self.port)
                _SERVERS[key] = server
            await server.register(self)
            self._server = server

    async def close(self) -> None:
        if self._server is None:
            return None
        lock = _server_registry_lock()
        async with lock:
            server = self._server
            await server.unregister(self)
            if server.is_unused:
                _SERVERS.pop((server.host, server.requested_port), None)
            self._server = None
        return None

    async def fetch(self, limit: int) -> list[Delivery]:
        queue = self._ensure_queue()
        fetch_limit = max(1, min(limit, self.batch_size))
        try:
            first = await asyncio.wait_for(queue.get(), timeout=self.poll_interval_s)
        except asyncio.TimeoutError:
            return []

        deliveries: list[Delivery] = [WebhookDelivery(self, first)]
        while len(deliveries) < fetch_limit:
            try:
                envelope = queue.get_nowait()
            except QueueEmpty:
                break
            deliveries.append(WebhookDelivery(self, envelope))
        return deliveries

    async def _enqueue(self, envelope: Envelope) -> None:
        await self._ensure_queue().put(envelope)

    def _enqueue_nowait(self, envelope: Envelope) -> None:
        self._ensure_queue().put_nowait(envelope)

    def _ensure_queue(self) -> Queue:
        loop = asyncio.get_running_loop()
        if self._queue is None or self._loop is not loop:
            self._queue = Queue(maxsize=self.queue_maxsize)
            self._loop = loop
        return self._queue

    def _build_event(
        self,
        *,
        method: str,
        target: str,
        headers: Mapping[str, str],
        body: bytes,
        client: tuple[str, int] | None,
    ) -> dict[str, Any]:
        parsed_target = urlsplit(target)
        return {
            "body": self._parse_body(body, headers),
            "headers": dict(headers),
            "query": _flatten_mapping(parse_qs(parsed_target.query, keep_blank_values=True)),
            "method": method,
            "path": parsed_target.path or "/",
            "client": {
                "host": client[0] if client else None,
                "port": client[1] if client else None,
            },
            "received_at": datetime.now(timezone.utc).isoformat(),
        }

    def _parse_body(self, body: bytes, headers: Mapping[str, str]) -> Any:
        if not body:
            return None
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()

        if self.parser == "raw":
            return body
        if self.parser == "text":
            return body.decode("utf-8", errors="replace")
        if self.parser == "form":
            return _flatten_mapping(parse_qs(body.decode("utf-8"), keep_blank_values=True))
        if self.parser == "json":
            return _load_json(body)

        if content_type == "application/x-www-form-urlencoded":
            return _flatten_mapping(parse_qs(body.decode("utf-8"), keep_blank_values=True))
        if content_type == "application/json" or content_type.endswith("+json"):
            return _load_json(body)
        if content_type.startswith("text/"):
            return body.decode("utf-8", errors="replace")

        try:
            return body.decode("utf-8")
        except UnicodeDecodeError:
            return body


class _WebhookServer:
    def __init__(self, host: str, requested_port: int) -> None:
        self.host = host
        self.requested_port = requested_port
        self._server: asyncio.AbstractServer | None = None
        self._routes: dict[tuple[str, str], WebhookSource] = {}
        self._methods_by_path: dict[str, set[str]] = {}

    @property
    def bound_port(self) -> int:
        if self._server is None or not self._server.sockets:
            return self.requested_port
        return int(self._server.sockets[0].getsockname()[1])

    @property
    def is_unused(self) -> bool:
        return not self._routes

    async def register(self, source: WebhookSource) -> None:
        for method in source.methods:
            key = (source.path, method)
            if key in self._routes and self._routes[key] is not source:
                raise ValueError(f"duplicate webhook route for {method} {source.path}")

        if self._server is None:
            self._server = await asyncio.start_server(self._handle_client, self.host, self.requested_port)

        for method in source.methods:
            key = (source.path, method)
            self._routes[key] = source
            self._methods_by_path.setdefault(source.path, set()).add(method)

    async def unregister(self, source: WebhookSource) -> None:
        for method in source.methods:
            key = (source.path, method)
            if self._routes.get(key) is source:
                self._routes.pop(key, None)
                methods = self._methods_by_path.get(source.path)
                if methods is not None:
                    methods.discard(method)
                    if not methods:
                        self._methods_by_path.pop(source.path, None)

        if not self._routes and self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            method, target, headers = await self._read_headers(reader)
            source, allowed_methods = self._resolve_route(method, target)
            if source is None:
                if allowed_methods is None:
                    await _write_response(writer, 404, {"error": "not_found"})
                else:
                    await _write_response(
                        writer,
                        405,
                        {"error": "method_not_allowed"},
                        headers={"Allow": ", ".join(sorted(allowed_methods))},
                    )
                return

            if source.auth is not None:
                source.auth.authorize(headers)
            body = await self._read_body(reader, headers, source)
            envelope = Envelope(
                body=source._build_event(
                    method=method,
                    target=target,
                    headers=headers,
                    body=body,
                    client=_peername(writer),
                ),
                meta={"source": source.name},
            )
            try:
                source._enqueue_nowait(envelope)
            except asyncio.QueueFull:
                await _write_response(writer, 503, {"error": "queue_full"})
                return

            status_code, response_headers, payload = source.response.render()
            await _write_response(writer, status_code, payload, headers=response_headers, body_is_bytes=True)
        except _HTTPError as exc:
            await _write_response(writer, exc.status_code, exc.body, headers=exc.headers)
        except Exception:
            await _write_response(writer, 500, {"error": "internal_server_error"})
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _read_headers(self, reader: asyncio.StreamReader) -> tuple[str, str, dict[str, str]]:
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=_DEFAULT_HEADER_TIMEOUT_S)
        except asyncio.TimeoutError as exc:
            raise _HTTPError(408, {"error": "request_timeout"}) from exc
        except asyncio.IncompleteReadError as exc:
            raise _HTTPError(400, {"error": "invalid_request"}) from exc

        text = raw.decode("iso-8859-1")
        lines = text.split("\r\n")
        request_line = lines[0]
        parts = request_line.split()
        if len(parts) != 3:
            raise _HTTPError(400, {"error": "invalid_request_line"})
        method, target, version = parts
        if not version.startswith("HTTP/1."):
            raise _HTTPError(400, {"error": "unsupported_http_version"})

        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line:
                continue
            if ":" not in line:
                raise _HTTPError(400, {"error": "invalid_header"})
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
        return method.upper(), target, headers

    async def _read_body(
        self,
        reader: asyncio.StreamReader,
        headers: Mapping[str, str],
        source: WebhookSource,
    ) -> bytes:
        if "transfer-encoding" in headers:
            raise _HTTPError(501, {"error": "transfer_encoding_not_supported"})

        content_length_text = headers.get("content-length")
        if content_length_text is None:
            return b""

        try:
            content_length = int(content_length_text)
        except ValueError as exc:
            raise _HTTPError(400, {"error": "invalid_content_length"}) from exc

        if content_length < 0:
            raise _HTTPError(400, {"error": "invalid_content_length"})
        if content_length > source.max_body_bytes:
            raise _HTTPError(413, {"error": "payload_too_large"})
        if content_length == 0:
            return b""

        try:
            return await asyncio.wait_for(reader.readexactly(content_length), timeout=source.read_timeout_s)
        except asyncio.TimeoutError as exc:
            raise _HTTPError(408, {"error": "request_timeout"}) from exc
        except asyncio.IncompleteReadError as exc:
            raise _HTTPError(400, {"error": "incomplete_body"}) from exc

    def _resolve_route(self, method: str, target: str) -> tuple[WebhookSource | None, set[str] | None]:
        path = _normalize_path(urlsplit(target).path or "/")
        route = self._routes.get((path, method))
        if route is not None:
            return route, None
        allowed_methods = self._methods_by_path.get(path)
        if allowed_methods is not None:
            return None, allowed_methods
        return None, None


class _HTTPError(Exception):
    def __init__(
        self,
        status_code: int,
        body: Any,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(status_code, body)
        self.status_code = status_code
        self.body = body
        self.headers = dict(headers or {})


def _normalize_path(path: str) -> str:
    if not path.startswith("/"):
        raise ValueError("path must start with '/'")
    if path != "/" and path.endswith("/"):
        return path.rstrip("/")
    return path or "/"


def _flatten_mapping(values: Mapping[str, list[str]]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, items in values.items():
        if len(items) == 1:
            flattened[key] = items[0]
        else:
            flattened[key] = items
    return flattened


def _load_json(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _HTTPError(400, {"error": "invalid_json"}) from exc


def _peername(writer: asyncio.StreamWriter) -> tuple[str, int] | None:
    peername = writer.get_extra_info("peername")
    if not isinstance(peername, tuple) or len(peername) < 2:
        return None
    return str(peername[0]), int(peername[1])


async def _write_response(
    writer: asyncio.StreamWriter,
    status_code: int,
    body: Any,
    *,
    headers: Mapping[str, str] | None = None,
    body_is_bytes: bool = False,
) -> None:
    response_headers = {str(key): str(value) for key, value in dict(headers or {}).items()}
    if body_is_bytes:
        payload = body if isinstance(body, bytes) else bytes(body)
        response_headers.setdefault("Content-Type", "application/octet-stream")
    elif body is None:
        payload = b""
    elif isinstance(body, bytes):
        payload = body
        response_headers.setdefault("Content-Type", "application/octet-stream")
    elif isinstance(body, str):
        payload = body.encode("utf-8")
        response_headers.setdefault("Content-Type", "text/plain; charset=utf-8")
    else:
        payload = json.dumps(body).encode("utf-8")
        response_headers.setdefault("Content-Type", "application/json")

    response_headers["Content-Length"] = str(len(payload))
    response_headers.setdefault("Connection", "close")

    reason = _STATUS_TEXT.get(status_code, "OK")
    lines = [f"HTTP/1.1 {status_code} {reason}"]
    for key, value in response_headers.items():
        lines.append(f"{key}: {value}")
    lines.append("")
    lines.append("")
    writer.write("\r\n".join(lines).encode("iso-8859-1") + payload)
    await writer.drain()


__all__ = [
    "BearerAuth",
    "WebhookResponse",
    "WebhookSource",
]
