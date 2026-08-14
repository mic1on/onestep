from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from onestep.envelope import Envelope
from onestep.connectors.base import Delivery, Sink, Source
from onestep.resilience import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError
from onestep.state import CursorStore, InMemoryCursorStore

_DEFAULT_BASE_URL = "https://open.feishu.cn"
_DEFAULT_TIMEOUT_S = 10.0
_DEFAULT_BATCH_SIZE = 100
DEFAULT_FALLBACK_SCAN_PAGE_LIMIT = 100
_MAX_PAGE_SIZE = 500
_TOKEN_REFRESH_MARGIN_S = 60.0
_REDACTED = "<redacted>"
_AUTOMATIC_CURSOR_FIELD_ALIASES = {
    "创建时间": "created_time",
    "最后修改时间": "last_modified_time",
    "最后更新时间": "last_modified_time",
}
_USER_ID_TYPES = frozenset({"open_id", "union_id", "user_id"})
_RELATION_FIELDS = frozenset({"from", "app_token", "table_id", "key", "on_missing", "create_fields"})
_RELATION_MISSING_POLICIES = frozenset({"error", "empty", "create"})


def feishu_bitable_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [feishu_bitable_text(item) for item in value]
        return "".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("text", "name", "value", "link", "email"):
            item = value.get(key)
            if item is not None:
                return feishu_bitable_text(item)
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def feishu_bitable_user(value: Any) -> list[dict[str, str]] | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return [{"id": normalized}] if normalized else None
    if isinstance(value, Mapping):
        user_id = _bitable_user_id(value)
        return [{"id": user_id}] if user_id else None
    if isinstance(value, list):
        users: list[dict[str, str]] = []
        for item in value:
            converted = feishu_bitable_user(item)
            if converted:
                users.extend(converted)
        return users or None
    raise TypeError("feishu_bitable_user value must be a string, mapping, list, or None")


class FeishuBitableApiError(RuntimeError):
    def __init__(
        self,
        *,
        status: int,
        reason: str,
        code: int | None,
        message: str,
        body: Any,
    ) -> None:
        self.status = status
        self.reason = reason
        self.code = code
        self.body = body
        super().__init__(message)


class FeishuBitablePayloadError(RuntimeError):
    pass


class FeishuBitableConnector:
    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        base_url: str = _DEFAULT_BASE_URL,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self.app_id = _require_non_empty_string(app_id, field="app_id")
        self.app_secret = _require_non_empty_string(app_secret, field="app_secret")
        self.base_url = _normalize_base_url(base_url)
        self.timeout_s = _normalize_timeout(timeout_s)
        self._tenant_access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock: asyncio.Lock | None = None

    async def close(self) -> None:
        return None

    def incremental(
        self,
        *,
        app_token: str,
        table_id: str,
        cursor_field: str,
        user_id_type: str | None = None,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        poll_interval_s: float = 1.0,
        fallback_scan_page_limit: int = DEFAULT_FALLBACK_SCAN_PAGE_LIMIT,
        state: CursorStore | None = None,
        state_key: str | None = None,
    ) -> "FeishuBitableIncrementalSource":
        return FeishuBitableIncrementalSource(
            connector=self,
            app_token=app_token,
            table_id=table_id,
            cursor_field=cursor_field,
            user_id_type=user_id_type,
            batch_size=batch_size,
            poll_interval_s=poll_interval_s,
            fallback_scan_page_limit=fallback_scan_page_limit,
            state=state or InMemoryCursorStore(),
            state_key=state_key
            or _default_incremental_state_key(
                app_token=app_token,
                table_id=table_id,
                cursor_field=cursor_field,
            ),
        )

    def table_sink(
        self,
        *,
        app_token: str,
        table_id: str,
        mode: str = "upsert",
        match_fields: Sequence[str] | None = None,
        user_id_type: str | None = None,
        relations: Mapping[str, Mapping[str, Any]] | None = None,
        batch_size: int = 1,
        flush_interval_s: float = 1.0,
    ) -> "FeishuBitableTableSink":
        return FeishuBitableTableSink(
            connector=self,
            app_token=app_token,
            table_id=table_id,
            mode=mode,
            match_fields=match_fields,
            user_id_type=user_id_type,
            relations=relations,
            batch_size=batch_size,
            flush_interval_s=flush_interval_s,
        )

    async def search_records(
        self,
        *,
        app_token: str,
        table_id: str,
        body: Mapping[str, Any],
        page_size: int,
        page_token: str | None = None,
        user_id_type: str | None = None,
        operation: ConnectorOperation,
        source_name: str,
        retry_delay_s: float | None = None,
    ) -> dict[str, Any]:
        query: dict[str, Any] = {"page_size": _normalize_page_size(page_size)}
        if page_token:
            query["page_token"] = page_token
        normalized_user_id_type = _normalize_user_id_type(user_id_type)
        if normalized_user_id_type is not None:
            query["user_id_type"] = normalized_user_id_type
        payload = await self._request_json(
            "POST",
            _bitable_records_path(app_token=app_token, table_id=table_id, suffix="/search"),
            query=query,
            body=dict(body),
            auth=True,
            operation=operation,
            source_name=source_name,
            retry_delay_s=retry_delay_s,
        )
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    async def create_record(
        self,
        *,
        app_token: str,
        table_id: str,
        fields: Mapping[str, Any],
        user_id_type: str | None = None,
        operation: ConnectorOperation,
        source_name: str,
        retry_delay_s: float | None = None,
    ) -> dict[str, Any]:
        _check_field_values_before_send(fields, table_id, source_name)
        try:
            payload = await self._request_json(
                "POST",
                _bitable_records_path(app_token=app_token, table_id=table_id),
                query=_user_id_type_query(user_id_type),
                body={"fields": dict(fields)},
                auth=True,
                operation=operation,
                source_name=source_name,
                retry_delay_s=retry_delay_s,
            )
        except ConnectorOperationError as exc:
            __traceback__ = exc.__traceback__
            raise _with_field_context(exc, fields, table_id) from exc.__cause__
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    async def update_record(
        self,
        *,
        app_token: str,
        table_id: str,
        record_id: str,
        fields: Mapping[str, Any],
        user_id_type: str | None = None,
        operation: ConnectorOperation,
        source_name: str,
        retry_delay_s: float | None = None,
    ) -> dict[str, Any]:
        _check_field_values_before_send(fields, table_id, source_name)
        path = _bitable_records_path(
            app_token=app_token,
            table_id=table_id,
            suffix=f"/{_quote_path(record_id)}",
        )
        try:
            payload = await self._request_json(
                "PUT",
                path,
                query=_user_id_type_query(user_id_type),
                body={"fields": dict(fields)},
                auth=True,
                operation=operation,
                source_name=source_name,
                retry_delay_s=retry_delay_s,
            )
        except ConnectorOperationError as exc:
            __traceback__ = exc.__traceback__
            raise _with_field_context(exc, fields, table_id) from exc.__cause__
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    async def batch_create_records(
        self,
        *,
        app_token: str,
        table_id: str,
        records: Sequence[dict[str, Any]],
        user_id_type: str | None = None,
        operation: ConnectorOperation,
        source_name: str,
        retry_delay_s: float | None = None,
    ) -> dict[str, Any]:
        """Create multiple records in one API call (max 500 per batch)."""
        if not records:
            return {"records": []}
        for fields in records:
            _check_field_values_before_send(fields, table_id, source_name)
        payload = await self._request_json(
            "POST",
            _bitable_records_path(app_token=app_token, table_id=table_id, suffix="/batch_create"),
            query=_user_id_type_query(user_id_type),
            body={"records": [{"fields": dict(fields)} for fields in records]},
            auth=True,
            operation=operation,
            source_name=source_name,
            retry_delay_s=retry_delay_s,
        )
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    async def batch_update_records(
        self,
        *,
        app_token: str,
        table_id: str,
        records: Sequence[dict[str, Any]],
        user_id_type: str | None = None,
        operation: ConnectorOperation,
        source_name: str,
        retry_delay_s: float | None = None,
    ) -> dict[str, Any]:
        """Update multiple records in one API call (max 500 per batch).

        Each record dict must include ``record_id`` and ``fields``.
        """
        if not records:
            return {"records": []}
        for item in records:
            _check_field_values_before_send(item["fields"], table_id, source_name)
        payload = await self._request_json(
            "POST",
            _bitable_records_path(app_token=app_token, table_id=table_id, suffix="/batch_update"),
            query=_user_id_type_query(user_id_type),
            body={
                "records": [
                    {"record_id": item["record_id"], "fields": dict(item["fields"])}
                    for item in records
                ]
            },
            auth=True,
            operation=operation,
            source_name=source_name,
            retry_delay_s=retry_delay_s,
        )
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    async def _tenant_token(
        self,
        *,
        operation: ConnectorOperation,
        source_name: str,
        retry_delay_s: float | None,
    ) -> str:
        now = time.monotonic()
        if self._tenant_access_token and now < self._token_expires_at - _TOKEN_REFRESH_MARGIN_S:
            return self._tenant_access_token
        lock = self._token_lock
        if lock is None:
            lock = asyncio.Lock()
            self._token_lock = lock
        async with lock:
            now = time.monotonic()
            if self._tenant_access_token and now < self._token_expires_at - _TOKEN_REFRESH_MARGIN_S:
                return self._tenant_access_token
            payload = await self._request_json(
                "POST",
                "/auth/v3/tenant_access_token/internal",
                body={"app_id": self.app_id, "app_secret": self.app_secret},
                auth=False,
                operation=operation,
                source_name=source_name,
                retry_delay_s=retry_delay_s,
            )
            token = payload.get("tenant_access_token")
            if not isinstance(token, str) or not token:
                raise ConnectorOperationError(
                    backend="feishu_bitable",
                    operation=operation,
                    kind=ConnectorErrorKind.MISCONFIGURED,
                    source_name=source_name,
                    retry_delay_s=retry_delay_s,
                    message="feishu_bitable token response did not include tenant_access_token",
                )
            expire = payload.get("expire", 7200)
            try:
                expire_s = max(1.0, float(expire))
            except (TypeError, ValueError):
                expire_s = 7200.0
            self._tenant_access_token = token
            self._token_expires_at = time.monotonic() + expire_s
            return token

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        auth: bool,
        operation: ConnectorOperation,
        source_name: str,
        retry_delay_s: float | None,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if auth:
            token = await self._tenant_token(
                operation=operation,
                source_name=source_name,
                retry_delay_s=retry_delay_s,
            )
            headers["Authorization"] = f"Bearer {token}"

        url = self._url(path, query=query)
        request_body = json.dumps(dict(body or {}), default=str).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=request_body,
            headers=headers,
            method=method.upper(),
        )
        try:
            status, reason, raw_body = await asyncio.to_thread(self._send_request, request)
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise ConnectorOperationError(
                backend="feishu_bitable",
                operation=operation,
                kind=_classify_transport_error(exc),
                source_name=source_name,
                retry_delay_s=retry_delay_s,
                cause=exc,
            ) from exc

        payload: Any
        if raw_body:
            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                error = FeishuBitableApiError(
                    status=status,
                    reason=reason,
                    code=None,
                    message=f"feishu_bitable returned invalid JSON for {source_name!r}",
                    body=raw_body.decode("utf-8", errors="replace"),
                )
                raise ConnectorOperationError(
                    backend="feishu_bitable",
                    operation=operation,
                    kind=_classify_status(status),
                    source_name=source_name,
                    retry_delay_s=retry_delay_s,
                    cause=error,
                ) from exc
        else:
            payload = {}

        if not isinstance(payload, dict):
            error = FeishuBitableApiError(
                status=status,
                reason=reason,
                code=None,
                message=f"feishu_bitable returned a non-object JSON response for {source_name!r}",
                body=payload,
            )
            raise ConnectorOperationError(
                backend="feishu_bitable",
                operation=operation,
                kind=_classify_status(status),
                source_name=source_name,
                retry_delay_s=retry_delay_s,
                cause=error,
            ) from error

        code = _optional_int(payload.get("code"))
        if status < 200 or status >= 300 or (code is not None and code != 0):
            message = str(payload.get("msg") or payload.get("message") or reason or "request failed")
            error = FeishuBitableApiError(
                status=status,
                reason=reason,
                code=code,
                message=message,
                body=payload,
            )
            raise ConnectorOperationError(
                backend="feishu_bitable",
                operation=operation,
                kind=_classify_api_error(status=status, code=code, message=message),
                source_name=source_name,
                retry_delay_s=retry_delay_s,
                cause=error,
                message=f"feishu_bitable {operation.value} failed for {source_name!r}: {message}",
            ) from error
        return payload

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

    def _url(self, path: str, *, query: Mapping[str, Any] | None = None) -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        url = f"{self.base_url}/open-apis{normalized_path}"
        if not query:
            return url
        return f"{url}?{urllib.parse.urlencode(query, doseq=True)}"


@dataclass
class _FeishuCursorToken:
    value: tuple[Any, str]


class FeishuBitableIncrementalDelivery(Delivery):
    def __init__(
        self,
        source: "FeishuBitableIncrementalSource",
        envelope: Envelope,
        token: _FeishuCursorToken,
    ) -> None:
        super().__init__(envelope)
        self._source = source
        self._token = token

    async def ack(self) -> None:
        await self._source.ack_token(self._token)

    async def retry(self, *, delay_s: float | None = None) -> None:
        if delay_s:
            await asyncio.sleep(delay_s)

    async def fail(self, exc: Exception | None = None) -> None:
        return None


class FeishuBitableIncrementalSource(Source):
    def __init__(
        self,
        *,
        connector: FeishuBitableConnector,
        app_token: str,
        table_id: str,
        cursor_field: str,
        user_id_type: str | None,
        batch_size: int,
        poll_interval_s: float,
        fallback_scan_page_limit: int,
        state: CursorStore,
        state_key: str,
    ) -> None:
        super().__init__(f"feishu_bitable.incremental:{table_id}")
        self.connector = connector
        self.app_token = _require_non_empty_string(app_token, field="app_token")
        self.table_id = _require_non_empty_string(table_id, field="table_id")
        self.cursor_field = _require_non_empty_string(cursor_field, field="cursor_field")
        self.user_id_type = _normalize_user_id_type(user_id_type)
        self.batch_size = _normalize_batch_size(batch_size)
        self.poll_interval_s = _normalize_poll_interval(poll_interval_s)
        self.fallback_scan_page_limit = _normalize_fallback_scan_page_limit(fallback_scan_page_limit)
        self.state = state
        self.state_key = state_key
        self._pending: deque[tuple[Any, str]] = deque()
        self._acked: set[tuple[Any, str]] = set()
        self._commit_lock: asyncio.Lock | None = None
        self._loaded = False
        self._committed_cursor: tuple[Any, str] | None = None
        self._fetched_cursor: tuple[Any, str] | None = None

    async def open(self) -> None:
        if self._loaded:
            return
        loaded = await self.state.load(self.state_key)
        if isinstance(loaded, (list, tuple)) and len(loaded) == 2 and isinstance(loaded[1], str):
            self._committed_cursor = (loaded[0], loaded[1])
            self._fetched_cursor = self._committed_cursor
        self._loaded = True

    async def fetch(self, limit: int) -> list[Delivery]:
        await self.open()
        page_size = max(1, min(int(limit), self.batch_size))
        try:
            records = await self._fetch_records(page_size)
        except ConnectorOperationError:
            raise
        except Exception as exc:
            raise ConnectorOperationError(
                backend="feishu_bitable",
                operation=ConnectorOperation.FETCH,
                kind=ConnectorErrorKind.PERMANENT,
                source_name=self.name,
                retry_delay_s=self.poll_interval_s,
                cause=exc,
            ) from exc

        deliveries: list[Delivery] = []
        for record in records:
            record_id = _record_id(record)
            fields = _record_fields(record)
            cursor_value = _record_cursor_value(record, self.cursor_field)
            if cursor_value is None:
                raise ConnectorOperationError(
                    backend="feishu_bitable",
                    operation=ConnectorOperation.FETCH,
                    kind=ConnectorErrorKind.PERMANENT,
                    source_name=self.name,
                    retry_delay_s=self.poll_interval_s,
                    message=f"feishu_bitable record {record_id!r} is missing cursor field {self.cursor_field!r}",
                )
            token = _FeishuCursorToken((cursor_value, record_id))
            self._pending.append(token.value)
            self._fetched_cursor = token.value
            body = {"record_id": record_id, "fields": fields}
            for automatic_field in ("created_time", "last_modified_time", "created_by", "last_modified_by"):
                if automatic_field in record:
                    body[automatic_field] = record[automatic_field]
            envelope = Envelope(
                body=body,
                meta={
                    "backend": "feishu_bitable",
                    "app_token": self.app_token,
                    "table_id": self.table_id,
                },
            )
            deliveries.append(FeishuBitableIncrementalDelivery(self, envelope, token))
        return deliveries

    async def ack_token(self, token: _FeishuCursorToken) -> None:
        lock = self._commit_lock
        if lock is None:
            lock = asyncio.Lock()
            self._commit_lock = lock
        async with lock:
            self._acked.add(token.value)
            advanced: tuple[Any, str] | None = None
            while self._pending and self._pending[0] in self._acked:
                advanced = self._pending.popleft()
                self._acked.remove(advanced)
            if advanced is not None:
                self._committed_cursor = advanced
                if not self._pending:
                    self._fetched_cursor = advanced
                await self.state.save(self.state_key, [advanced[0], advanced[1]])

    def control_plane_descriptor(self) -> dict[str, Any]:
        return {
            "kind": "feishu_bitable_incremental",
            "name": self.name,
            "config": {
                "base_url": _redact_url(self.connector.base_url),
                "app_token": _redact_token(self.app_token),
                "table_id": self.table_id,
                "cursor_field": self.cursor_field,
                "user_id_type": self.user_id_type,
                "batch_size": self.batch_size,
                "poll_interval_s": self.poll_interval_s,
                "fallback_scan_page_limit": self.fallback_scan_page_limit,
                "state_key": self.state_key,
            },
        }

    async def _fetch_records(self, limit: int) -> list[dict[str, Any]]:
        try:
            return await self._fetch_records_with_body(
                limit,
                body=_incremental_search_body(self.cursor_field, sort=True),
                scan_all_pages=False,
            )
        except ConnectorOperationError as exc:
            if not _is_search_shape_error(exc):
                raise
            return await self._fetch_records_with_body(
                limit,
                body=_incremental_search_body(self.cursor_field, sort=False),
                scan_all_pages=True,
            )

    async def _fetch_records_with_body(
        self,
        limit: int,
        *,
        body: Mapping[str, Any],
        scan_all_pages: bool,
    ) -> list[dict[str, Any]]:
        read_cursor = self._fetched_cursor or self._committed_cursor
        page_token: str | None = None
        records: list[dict[str, Any]] = []
        pages_scanned = 0
        while True:
            data = await self.connector.search_records(
                app_token=self.app_token,
                table_id=self.table_id,
                body=body,
                page_size=limit,
                page_token=page_token,
                user_id_type=self.user_id_type,
                operation=ConnectorOperation.FETCH,
                source_name=self.name,
                retry_delay_s=self.poll_interval_s,
            )
            pages_scanned += 1
            raw_items = data.get("items", [])
            if not isinstance(raw_items, list):
                raise FeishuBitablePayloadError("feishu_bitable search response data.items must be a list")
            candidates = []
            for item in raw_items:
                if not isinstance(item, Mapping):
                    continue
                record = dict(item)
                cursor_value = _record_cursor_value(record, self.cursor_field)
                if cursor_value is None:
                    raise FeishuBitablePayloadError(
                        f"feishu_bitable record {_record_id(record)!r} is missing cursor field {self.cursor_field!r}"
                    )
                token = (cursor_value, _record_id(record))
                if read_cursor is None or _cursor_after(token, read_cursor):
                    candidates.append(record)
            candidates.sort(
                key=lambda item: _cursor_sort_key((_record_cursor_value(item, self.cursor_field), _record_id(item)))
            )
            for record in candidates:
                if not scan_all_pages and len(records) >= limit:
                    break
                records.append(record)
            has_more = bool(data.get("has_more"))
            next_page_token = data.get("page_token")
            page_token = next_page_token if isinstance(next_page_token, str) and next_page_token else None
            if scan_all_pages and has_more and page_token and pages_scanned >= self.fallback_scan_page_limit:
                raise ConnectorOperationError(
                    backend="feishu_bitable",
                    operation=ConnectorOperation.FETCH,
                    kind=ConnectorErrorKind.PERMANENT,
                    source_name=self.name,
                    retry_delay_s=self.poll_interval_s,
                    message=(
                        "feishu_bitable incremental fallback scan exceeded "
                        f"fallback_scan_page_limit={self.fallback_scan_page_limit}; "
                        "make the cursor field sortable or increase fallback_scan_page_limit"
                    ),
                )
            if not has_more or not page_token:
                break
            if not scan_all_pages and len(records) >= limit:
                break
        records.sort(key=lambda item: _cursor_sort_key((_record_cursor_value(item, self.cursor_field), _record_id(item))))
        return records[:limit]


@dataclass(frozen=True)
class _FeishuRelationConfig:
    target_field: str
    source_field: str
    app_token: str
    table_id: str
    key: str
    on_missing: str
    create_fields: Mapping[str, Any]


@dataclass
class _FeishuRelationCreateLock:
    lock: asyncio.Lock
    users: int = 0
    record_id: str | None = None


class FeishuBitableTableSink(Sink):
    """Feishu Bitable table sink with optional batch buffering for create mode.

    When ``mode="create"`` and ``batch_size > 1``, records are buffered and
    flushed in batches via the Feishu ``batch_create`` API.  This dramatically
    reduces API calls — 500 records become 1 call instead of 500.

    upsert and update modes still process records one at a time because
    each record requires a match-finding search before the write.
    """

    def __init__(
        self,
        *,
        connector: FeishuBitableConnector,
        app_token: str,
        table_id: str,
        mode: str,
        match_fields: Sequence[str] | None,
        user_id_type: str | None,
        relations: Mapping[str, Mapping[str, Any]] | None = None,
        batch_size: int = 1,
        flush_interval_s: float = 1.0,
    ) -> None:
        super().__init__(f"feishu_bitable.table_sink:{table_id}")
        normalized_mode = _normalize_mode(mode)
        if normalized_mode in {"upsert", "update", "insert"}:
            normalized_match_fields = _normalize_match_fields(match_fields, required=True)
        else:
            normalized_match_fields = _normalize_match_fields(match_fields, required=False)
        self.connector = connector
        normalized_app_token = _require_non_empty_string(app_token, field="app_token")
        self.app_token = normalized_app_token
        self.table_id = _require_non_empty_string(table_id, field="table_id")
        self.mode = normalized_mode
        self.match_fields = normalized_match_fields
        self.user_id_type = _normalize_user_id_type(user_id_type)
        self.relations = _normalize_relations(
            relations,
            default_app_token=normalized_app_token,
            match_fields=normalized_match_fields,
        )
        self._relation_create_locks: dict[tuple[str, str, str, str], _FeishuRelationCreateLock] = {}
        self._batch_size = max(1, min(batch_size, _MAX_PAGE_SIZE))
        self._flush_interval_s = float(flush_interval_s)
        # Buffer stores raw payload fields (before relation resolution & match-finding)
        self._buffer: list[dict[str, Any]] = []
        self._buffer_lock: asyncio.Lock | None = None
        self._flush_task: asyncio.Task[None] | None = None
        self._flush_error: Exception | None = None
        self._closed = False

    def _ensure_buffer_lock(self) -> asyncio.Lock:
        if self._buffer_lock is None:
            self._buffer_lock = asyncio.Lock()
        return self._buffer_lock

    async def send(self, envelope: Envelope) -> None:
        """Buffer raw fields for batch processing."""
        try:
            raw_fields = _payload_fields(envelope.body)
            await self._buffer_record(raw_fields)
        except ConnectorOperationError:
            raise
        except FeishuBitablePayloadError as exc:
            raise ConnectorOperationError(
                backend="feishu_bitable",
                operation=ConnectorOperation.SEND,
                kind=ConnectorErrorKind.PERMANENT,
                source_name=self.name,
                retry_delay_s=1.0,
                cause=exc,
                message=f"feishu_bitable send failed (permanent) for {self.name!r}: {exc}",
            ) from exc

    async def _buffer_record(self, raw_fields: dict[str, Any]) -> None:
        """Buffer a record for batch write, or send immediately if batch_size <= 1."""
        if self._batch_size <= 1:
            await self._send_single(raw_fields)
            return

        lock = self._ensure_buffer_lock()
        async with lock:
            # Surface any pending flush error from a background timer flush
            if self._flush_error is not None:
                err = self._flush_error
                self._flush_error = None
                raise err

            self._buffer.append(raw_fields)
            if len(self._buffer) >= self._batch_size:
                await self._flush_buffer()
                return
            if self._flush_task is None and not self._closed:
                self._flush_task = asyncio.ensure_future(self._flush_after_interval())

    async def _send_single(self, raw_fields: dict[str, Any]) -> None:
        """Send a single record immediately (batch_size=1 path)."""
        fields = await self._resolve_relation_fields(raw_fields)
        if self.mode == "create":
            await self.connector.create_record(
                app_token=self.app_token,
                table_id=self.table_id,
                fields=fields,
                user_id_type=self.user_id_type,
                operation=ConnectorOperation.SEND,
                source_name=self.name,
                retry_delay_s=1.0,
            )
            return

        match_values = _match_values(fields, self.match_fields)
        matches = await self._find_matches(match_values)
        if len(matches) > 1:
            raise FeishuBitablePayloadError(
                f"{self.mode} match fields {self.match_fields!r} matched {len(matches)} records"
            )
        if matches:
            if self.mode == "insert":
                return  # skip: record already exists
            await self.connector.update_record(
                app_token=self.app_token,
                table_id=self.table_id,
                record_id=_record_id(matches[0]),
                fields=fields,
                user_id_type=self.user_id_type,
                operation=ConnectorOperation.SEND,
                source_name=self.name,
                retry_delay_s=1.0,
            )
            return
        if self.mode == "update":
            raise FeishuBitablePayloadError(f"no record matched fields {self.match_fields!r}")
        await self.connector.create_record(
            app_token=self.app_token,
            table_id=self.table_id,
            fields=fields,
            user_id_type=self.user_id_type,
            operation=ConnectorOperation.SEND,
            source_name=self.name,
            retry_delay_s=1.0,
        )

    async def _flush_after_interval(self) -> None:
        """Flush the buffer after flush_interval_s of inactivity."""
        try:
            await asyncio.sleep(self._flush_interval_s)
            lock = self._ensure_buffer_lock()
            async with lock:
                if self._buffer and self._flush_error is None:
                    await self._flush_buffer()
                self._flush_task = None
        except asyncio.CancelledError:
            self._flush_task = None
            raise
        except BaseException as exc:
            self._flush_task = None
            self._flush_error = exc

    async def _flush_buffer(self) -> None:
        """Flush buffered records with batched relation resolution and write.

        The buffer is only cleared after a successful API write.  On failure
        records remain in the buffer so they can be retried on the next flush.
        """
        if not self._buffer:
            return
        items = self._buffer[:]
        if self._flush_task is not None:
            self._flush_task.cancel()
            self._flush_task = None

        # Step 1: Batch resolve relations with dedup + concurrent search
        if self.relations:
            resolved = await self._batch_resolve_relations(items)
        else:
            resolved = [dict(r) for r in items]

        # Step 2: For upsert/update/insert, batch find matches
        if self.mode == "create":
            creates = resolved
            updates: list[dict[str, Any]] = []
        else:
            creates, updates = await self._batch_match_and_split(resolved)

        # Step 3: Batch write (on failure, buffer is preserved)
        try:
            if creates:
                await self.connector.batch_create_records(
                    app_token=self.app_token,
                    table_id=self.table_id,
                    records=creates,
                    user_id_type=self.user_id_type,
                    operation=ConnectorOperation.SEND,
                    source_name=self.name,
                    retry_delay_s=1.0,
                )
            if updates:
                await self.connector.batch_update_records(
                    app_token=self.app_token,
                    table_id=self.table_id,
                    records=updates,
                    user_id_type=self.user_id_type,
                    operation=ConnectorOperation.SEND,
                    source_name=self.name,
                    retry_delay_s=1.0,
                )
        except Exception:
            # Buffer is still intact (items was a copy), nothing to restore
            raise

        # Only clear after successful API write
        self._buffer.clear()

    async def _batch_resolve_relations(
        self, items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Resolve relation fields for all records in batch.

        Collects all unique (relation, value) pairs, searches concurrently
        with a semaphore, and builds a cache to avoid repeated API calls.
        """
        _SEARCH_CONCURRENCY = 20
        sem = asyncio.Semaphore(_SEARCH_CONCURRENCY)
        # {(target_field, value): record_id | None}
        cache: dict[tuple[str, str], str | None] = {}
        # List of (relation, value) for create-on-missing values
        to_create: list[tuple[_FeishuRelationConfig, str]] = []

        async def search_one(rel: _FeishuRelationConfig, value: str) -> None:
            async with sem:
                matches = await self._find_relation_matches(rel, value)
                if len(matches) > 1:
                    raise FeishuBitablePayloadError(
                        f"relation field {rel.target_field!r} value {value!r} "
                        f"matched {len(matches)} records in table {rel.table_id!r}"
                    )
                if matches:
                    cache[(rel.target_field, value)] = _record_id(matches[0])
                elif rel.on_missing == "create":
                    to_create.append((rel, value))
                elif rel.on_missing == "error":
                    raise FeishuBitablePayloadError(
                        f"relation field {rel.target_field!r} value {value!r} "
                        f"did not match a record in table {rel.table_id!r}"
                    )
                # on_missing == "empty": just skip, cache stays empty

        # Collect unique (relation, value) pairs, keyed by (target_field, value)
        pending: dict[tuple[str, str], tuple[_FeishuRelationConfig, str]] = {}
        for item in items:
            for relation in self.relations:
                values = _normalize_relation_values(
                    item.get(relation.source_field), field=relation.source_field
                )
                for value in values:
                    pending[(relation.target_field, value)] = (relation, value)

        if pending:
            tasks = [search_one(rel, val) for rel, val in pending.values()]
            await asyncio.gather(*tasks)

        # Batch create missing records
        if to_create:
            await self._batch_create_relation_records(to_create, cache)

        # Build resolved fields for each record
        result: list[dict[str, Any]] = []
        for item in items:
            resolved: dict[str, list[str]] = {}
            consumed: set[str] = set()
            for relation in self.relations:
                values = _normalize_relation_values(
                    item.get(relation.source_field), field=relation.source_field
                )
                record_ids: list[str] = []
                for value in values:
                    rid = cache.get((relation.target_field, value))
                    if rid:
                        record_ids.append(rid)
                resolved[relation.target_field] = record_ids
                if relation.source_field != relation.target_field:
                    consumed.add(relation.source_field)

            out = dict(item)
            for field_name in consumed:
                out.pop(field_name, None)
            out.update(resolved)
            result.append(out)

        return result

    async def _batch_create_relation_records(
        self,
        to_create: list[tuple[_FeishuRelationConfig, str]],
        cache: dict[tuple[str, str], str | None],
    ) -> None:
        """Batch create missing relation records and update the cache."""
        if not to_create:
            return
        # Group by (app_token, table_id, key)
        groups: dict[tuple[str, str, str], list[tuple[_FeishuRelationConfig, str]]] = {}
        for rel, value in to_create:
            groups.setdefault((rel.app_token, rel.table_id, rel.key), []).append((rel, value))

        for (app_token, table_id, key), entries in groups.items():
            records = [
                {key: value, **dict(rel.create_fields)}
                for rel, value in entries
            ]
            result = await self.connector.batch_create_records(
                app_token=app_token,
                table_id=table_id,
                records=records,
                user_id_type=self.user_id_type,
                operation=ConnectorOperation.SEND,
                source_name=self.name,
                retry_delay_s=1.0,
            )
            raw_records = result.get("records", [])
            if isinstance(raw_records, list):
                for i, rec in enumerate(raw_records):
                    if isinstance(rec, dict) and isinstance(rec.get("fields"), dict):
                        rel, value = entries[i]
                        cache[(rel.target_field, value)] = rec["record_id"]

    async def _batch_match_and_split(
        self, resolved: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Find matches for all records and split into creates and updates."""
        _SEARCH_CONCURRENCY = 20
        sem = asyncio.Semaphore(_SEARCH_CONCURRENCY)
        # {(match_values_tuple): record_id|None}
        cache: dict[tuple[tuple[str, Any], ...], str | None] = {}

        async def match_one(match_values: dict[str, Any]) -> str | None:
            async with sem:
                matches = await self._find_matches(match_values)
                if len(matches) > 1:
                    raise FeishuBitablePayloadError(
                        f"{self.mode} match fields {self.match_fields!r} matched {len(matches)} records"
                    )
                if matches:
                    return _record_id(matches[0])
                return None

        # Deduplicate match values
        index: list[tuple[int, tuple[tuple[str, Any], ...], dict[str, Any]]] = []
        for i, fields in enumerate(resolved):
            mv = _match_values(fields, self.match_fields)
            key = tuple(sorted(mv.items()))
            cache.setdefault(key, ...)
            index.append((i, key, mv))

        # Concurrent search for unique match values
        pending = {k: v for k, v in cache.items() if v is ...}
        if pending:
            tasks = [match_one(dict(k)) for k in pending]
            results = await asyncio.gather(*tasks)
            for key, rid in zip(pending, results):
                cache[key] = rid

        # Split into creates and updates
        creates: list[dict[str, Any]] = []
        updates: list[dict[str, Any]] = []
        for i, key, _ in index:
            rid = cache[key]
            if rid is None:
                if self.mode == "update":
                    raise FeishuBitablePayloadError(
                        f"no record matched fields {self.match_fields!r}"
                    )
                creates.append(resolved[i])
            elif self.mode == "insert":
                continue  # skip: record already exists
            else:
                updates.append({"record_id": rid, "fields": resolved[i]})

        return creates, updates

    async def close(self) -> None:
        """Flush remaining buffered records before closing."""
        self._closed = True
        lock = self._ensure_buffer_lock()
        async with lock:
            if self._flush_task is not None:
                self._flush_task.cancel()
                self._flush_task = None
            if self._buffer:
                await self._flush_buffer()
            if self._flush_error is not None:
                err = self._flush_error
                self._flush_error = None
                raise err

    async def _resolve_relation_fields(self, fields: Mapping[str, Any]) -> dict[str, Any]:
        if not self.relations:
            return dict(fields)
        original = dict(fields)
        resolved: dict[str, list[str]] = {}
        consumed_fields: set[str] = set()
        for relation in self.relations:
            values = _normalize_relation_values(original.get(relation.source_field), field=relation.source_field)
            record_ids: list[str] = []
            for value in values:
                matches = await self._find_relation_matches(relation, value)
                if len(matches) > 1:
                    raise FeishuBitablePayloadError(
                        f"relation field {relation.target_field!r} value {value!r} "
                        f"matched {len(matches)} records in table {relation.table_id!r}"
                    )
                if matches:
                    record_ids.append(_record_id(matches[0]))
                    continue
                if relation.on_missing == "error":
                    raise FeishuBitablePayloadError(
                        f"relation field {relation.target_field!r} value {value!r} "
                        f"did not match a record in table {relation.table_id!r}"
                    )
                if relation.on_missing == "create":
                    record_ids.append(await self._find_or_create_relation_record(relation, value))
            resolved[relation.target_field] = record_ids
            if relation.source_field != relation.target_field:
                consumed_fields.add(relation.source_field)

        result = dict(original)
        for field_name in consumed_fields:
            result.pop(field_name, None)
        result.update(resolved)
        return result

    async def _find_or_create_relation_record(
        self,
        relation: _FeishuRelationConfig,
        value: str,
    ) -> str:
        lock_key = (relation.app_token, relation.table_id, relation.key, value)
        entry = self._relation_create_locks.get(lock_key)
        if entry is None:
            entry = _FeishuRelationCreateLock(asyncio.Lock())
            self._relation_create_locks[lock_key] = entry
        entry.users += 1
        try:
            async with entry.lock:
                if entry.record_id is not None:
                    return entry.record_id
                matches = await self._find_relation_matches(relation, value)
                if len(matches) > 1:
                    raise FeishuBitablePayloadError(
                        f"relation field {relation.target_field!r} value {value!r} "
                        f"matched {len(matches)} records in table {relation.table_id!r}"
                    )
                if matches:
                    return _record_id(matches[0])
                fields = dict(relation.create_fields)
                fields[relation.key] = value
                data = await self.connector.create_record(
                    app_token=relation.app_token,
                    table_id=relation.table_id,
                    fields=fields,
                    user_id_type=self.user_id_type,
                    operation=ConnectorOperation.SEND,
                    source_name=self.name,
                    retry_delay_s=1.0,
                )
                raw_record = data.get("record")
                if not isinstance(raw_record, Mapping):
                    raise FeishuBitablePayloadError(
                        f"feishu_bitable create response for relation field {relation.target_field!r} "
                        "is missing record"
                    )
                entry.record_id = _record_id(raw_record)
                return entry.record_id
        finally:
            entry.users -= 1
            if entry.users == 0 and self._relation_create_locks.get(lock_key) is entry:
                self._relation_create_locks.pop(lock_key, None)

    async def _find_relation_matches(
        self,
        relation: _FeishuRelationConfig,
        value: str,
    ) -> list[dict[str, Any]]:
        data = await self.connector.search_records(
            app_token=relation.app_token,
            table_id=relation.table_id,
            body=_match_search_body({relation.key: value}),
            page_size=2,
            user_id_type=self.user_id_type,
            operation=ConnectorOperation.SEND,
            source_name=self.name,
            retry_delay_s=1.0,
        )
        raw_items = data.get("items", [])
        if not isinstance(raw_items, list):
            raise FeishuBitablePayloadError("feishu_bitable search response data.items must be a list")
        if any(not isinstance(item, Mapping) for item in raw_items):
            raise FeishuBitablePayloadError(
                "feishu_bitable search response data.items entries must be mappings"
            )
        return [dict(item) for item in raw_items]

    def control_plane_descriptor(self) -> dict[str, Any]:
        return {
            "kind": "feishu_bitable_table_sink",
            "name": self.name,
            "config": {
                "base_url": _redact_url(self.connector.base_url),
                "app_token": _redact_token(self.app_token),
                "table_id": self.table_id,
                "mode": self.mode,
                "match_fields": list(self.match_fields),
                "user_id_type": self.user_id_type,
                "relations": [
                    {
                        "target_field": relation.target_field,
                        "from": relation.source_field,
                        "table_id": relation.table_id,
                        "key": relation.key,
                        "on_missing": relation.on_missing,
                        "create_field_names": sorted(relation.create_fields),
                        "uses_custom_app_token": relation.app_token != self.app_token,
                    }
                    for relation in self.relations
                ],
            },
        }

    async def _find_matches(self, match_values: Mapping[str, Any]) -> list[dict[str, Any]]:
        data = await self.connector.search_records(
            app_token=self.app_token,
            table_id=self.table_id,
            body=_match_search_body(match_values),
            page_size=2,
            user_id_type=self.user_id_type,
            operation=ConnectorOperation.SEND,
            source_name=self.name,
            retry_delay_s=1.0,
        )
        raw_items = data.get("items", [])
        if not isinstance(raw_items, list):
            raise FeishuBitablePayloadError("feishu_bitable search response data.items must be a list")
        return [dict(item) for item in raw_items if isinstance(item, Mapping)]


def _bitable_records_path(*, app_token: str, table_id: str, suffix: str = "") -> str:
    return (
        f"/bitable/v1/apps/{_quote_path(app_token)}"
        f"/tables/{_quote_path(table_id)}"
        f"/records{suffix}"
    )


def _incremental_search_body(cursor_field: str, *, sort: bool) -> dict[str, Any]:
    body: dict[str, Any] = {"automatic_fields": True}
    if sort:
        body["sort"] = [{"field_name": _cursor_field_name(cursor_field), "desc": False}]
    return body


def _match_search_body(match_values: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "filter": {
            "conjunction": "and",
            "conditions": [
                {
                    "field_name": field_name,
                    "operator": "is",
                    "value": [field_value],
                }
                for field_name, field_value in match_values.items()
            ],
        }
    }


def _payload_fields(body: Any) -> dict[str, Any]:
    if not isinstance(body, Mapping):
        raise FeishuBitablePayloadError("FeishuBitableTableSink only accepts mapping payloads")
    raw_fields = body.get("fields")
    if isinstance(raw_fields, Mapping):
        return dict(raw_fields)
    return dict(body)


def _bitable_user_id(value: Mapping[str, Any]) -> str | None:
    for key in ("id", "user_id", "open_id", "union_id"):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return None


def _record_id(record: Mapping[str, Any]) -> str:
    value = record.get("record_id")
    if not isinstance(value, str) or not value:
        raise FeishuBitablePayloadError("feishu_bitable record is missing record_id")
    return value


def _record_fields(record: Mapping[str, Any]) -> dict[str, Any]:
    fields = record.get("fields")
    if not isinstance(fields, Mapping):
        raise FeishuBitablePayloadError("feishu_bitable record is missing fields")
    return dict(fields)


def _record_cursor_value(record: Mapping[str, Any], cursor_field: str) -> Any | None:
    fields = _record_fields(record)
    if cursor_field in fields:
        return fields[cursor_field]
    automatic_field = _AUTOMATIC_CURSOR_FIELD_ALIASES.get(cursor_field, cursor_field)
    return record.get(automatic_field)


def _cursor_field_name(cursor_field: str) -> str:
    return _AUTOMATIC_CURSOR_FIELD_ALIASES.get(cursor_field, cursor_field)


def _cursor_after(value: tuple[Any, str], cursor: tuple[Any, str]) -> bool:
    return _cursor_sort_key(value) > _cursor_sort_key(cursor)


def _cursor_sort_key(value: tuple[Any, str]) -> tuple[tuple[int, Any], str]:
    return (_cursor_value_sort_key(value[0]), value[1])


def _cursor_value_sort_key(value: Any) -> tuple[int, Any]:
    if isinstance(value, bool):
        return (2, str(value))
    if isinstance(value, (int, float)):
        return (0, float(value))
    return (1, str(value))


def _default_incremental_state_key(*, app_token: str, table_id: str, cursor_field: str) -> str:
    return f"feishu_bitable:{_short_token(app_token)}:{table_id}:cursor={cursor_field}"


def _normalize_base_url(value: str) -> str:
    normalized = _require_non_empty_string(value, field="base_url").rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("'base_url' must be an http or https URL")
    if parsed.path not in {"", "/"}:
        raise ValueError("'base_url' must not include a path")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _normalize_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("'timeout_s' must be a number")
    normalized = float(value)
    if normalized <= 0:
        raise ValueError("'timeout_s' must be > 0")
    return normalized


def _normalize_batch_size(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("'batch_size' must be an integer")
    if value < 1:
        raise ValueError("'batch_size' must be >= 1")
    return min(value, _MAX_PAGE_SIZE)


def _normalize_page_size(value: int) -> int:
    return max(1, min(int(value), _MAX_PAGE_SIZE))


def _normalize_poll_interval(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("'poll_interval_s' must be a number")
    normalized = float(value)
    if normalized < 0:
        raise ValueError("'poll_interval_s' must be >= 0")
    return normalized


def _normalize_fallback_scan_page_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("'fallback_scan_page_limit' must be an integer")
    if value < 1:
        raise ValueError("'fallback_scan_page_limit' must be >= 1")
    return value


def _normalize_mode(value: str) -> str:
    normalized = _require_non_empty_string(value, field="mode").strip().lower()
    if normalized not in {"upsert", "create", "update", "insert"}:
        raise ValueError("mode must be one of 'upsert', 'create', 'update', or 'insert'")
    return normalized


def _normalize_user_id_type(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _require_non_empty_string(value, field="user_id_type").strip().lower()
    if normalized not in _USER_ID_TYPES:
        raise ValueError("user_id_type must be one of 'open_id', 'union_id', or 'user_id'")
    return normalized


def _user_id_type_query(value: str | None) -> dict[str, str] | None:
    normalized = _normalize_user_id_type(value)
    return {"user_id_type": normalized} if normalized is not None else None


def _require_non_empty_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{field}' must be a non-empty string")
    return value.strip()


def _empty_match_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, tuple, set, dict)) and not value:
        return True
    return False


def _normalize_match_fields(value: Sequence[str] | None, *, required: bool) -> tuple[str, ...]:
    if value is None:
        if required:
            raise ValueError("'match_fields' must be a non-empty list of strings")
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError("'match_fields' must be a non-empty list of strings")
    fields = tuple(_require_non_empty_string(item, field="match_fields") for item in value)
    if not fields and required:
        raise ValueError("'match_fields' must be a non-empty list of strings")
    if len(set(fields)) != len(fields):
        raise ValueError("'match_fields' must not contain duplicate field names")
    return fields


def _normalize_relations(
    value: Mapping[str, Mapping[str, Any]] | None,
    *,
    default_app_token: str,
    match_fields: Sequence[str],
) -> tuple[_FeishuRelationConfig, ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise TypeError("'relations' must be a mapping")
    if not value:
        raise ValueError("'relations' must be a non-empty mapping")

    normalized: list[_FeishuRelationConfig] = []
    for raw_target_field, raw_config in value.items():
        target_field = _require_non_empty_string(raw_target_field, field="relations target field")
        field = f"relations.{target_field}"
        if not isinstance(raw_config, Mapping):
            raise TypeError(f"'{field}' must be a mapping")
        unknown_fields = sorted(str(item) for item in raw_config if item not in _RELATION_FIELDS)
        if unknown_fields:
            raise ValueError(f"unsupported fields for {field}: {', '.join(unknown_fields)}")

        source_field = _require_non_empty_string(raw_config.get("from", target_field), field=f"{field}.from")
        app_token = _require_non_empty_string(
            raw_config.get("app_token", default_app_token),
            field=f"{field}.app_token",
        )
        table_id = _require_non_empty_string(raw_config.get("table_id"), field=f"{field}.table_id")
        key = _require_non_empty_string(raw_config.get("key"), field=f"{field}.key")
        on_missing = _require_non_empty_string(
            raw_config.get("on_missing", "error"),
            field=f"{field}.on_missing",
        ).lower()
        if on_missing not in _RELATION_MISSING_POLICIES:
            raise ValueError(f"'{field}.on_missing' must be one of 'error', 'empty', or 'create'")

        raw_create_fields = raw_config.get("create_fields", {})
        if not isinstance(raw_create_fields, Mapping):
            raise TypeError(f"'{field}.create_fields' must be a mapping")
        if "create_fields" in raw_config and on_missing != "create":
            raise ValueError(f"'{field}.create_fields' requires on_missing 'create'")
        create_fields = dict(raw_create_fields)
        if any(not isinstance(field_name, str) or not field_name.strip() for field_name in create_fields):
            raise ValueError(f"'{field}.create_fields' keys must be non-empty strings")
        if key in create_fields:
            raise ValueError(f"'{field}.create_fields' must not contain relation key {key!r}")

        if target_field in match_fields:
            raise ValueError(f"relation target field {target_field!r} must not appear in match_fields")
        if source_field != target_field and source_field in match_fields:
            raise ValueError(f"relation source field {source_field!r} must not appear in match_fields")

        normalized.append(
            _FeishuRelationConfig(
                target_field=target_field,
                source_field=source_field,
                app_token=app_token,
                table_id=table_id,
                key=key,
                on_missing=on_missing,
                create_fields=MappingProxyType(create_fields),
            )
        )
    return tuple(normalized)


def _normalize_relation_values(value: Any, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw_values = (value,)
    elif isinstance(value, (int, float, bool)):
        raw_values = (str(value),)
    elif isinstance(value, (list, tuple)):
        raw_values = tuple(value)
    else:
        raise FeishuBitablePayloadError(
            f"relation source field {field!r} must be a string, number, list, tuple, or None"
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        if item is None:
            continue
        if isinstance(item, (int, float, bool)):
            item = str(item)
        if not isinstance(item, str):
            raise FeishuBitablePayloadError(
                f"relation source field {field!r} values must be strings, numbers, or None"
            )
        item = item.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return tuple(normalized)


def _match_values(fields: Mapping[str, Any], match_fields: Sequence[str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field_name in match_fields:
        field_value = fields.get(field_name)
        if _empty_match_value(field_value):
            raise FeishuBitablePayloadError(f"payload must include non-empty match_fields entry {field_name!r}")
        values[field_name] = field_value
    return values


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_KNOWN_DICT_FIELD_KEYS = frozenset({
    "text",           # rich text object
    "name",           # text field common key
    "value",          # text field common key
    "link",           # URL field
    "email",          # email field
    "id",             # person field
    "file_token",     # attachment field
    "location",       # location field
    "address",        # location field
    "elements",       # block/rich text
    "type",           # rich text type discriminator
})


def _check_field_values_before_send(
    fields: Mapping[str, Any],
    table_id: str,
    source_name: str,
) -> None:
    """Warn about field values that may cause TextFieldConvFail.

    Feishu's TextFieldConvFail error does not report which field caused the
    failure.  This check emits a warning for the most common cause — a bare
    dict being sent to a text field — so the field name and value are logged
    even if the API call fails with a generic error.
    """
    _logger = logging.getLogger(__name__)
    for field_name, value in fields.items():
        if isinstance(value, dict) and not _KNOWN_DICT_FIELD_KEYS.intersection(value):
            _logger.warning(
                "Field %r looks like a dict with no recognized Feishu field-type keys "
                "(keys=%r). If this is meant for a text field, use feishu_bitable_text() "
                "to convert it. (table_id=%s, source=%s)",
                field_name,
                sorted(value.keys()),
                table_id,
                source_name,
            )


def _with_field_context(
    exc: ConnectorOperationError,
    fields: Mapping[str, Any],
    table_id: str,
) -> ConnectorOperationError:
    field_names = list(fields.keys())
    cause = exc.__cause__
    if isinstance(cause, FeishuBitableApiError) and isinstance(cause.body, dict):
        api_data = cause.body.get("data")
        if isinstance(api_data, dict):
            api_fields = api_data.get("field_name") or api_data.get("field") or api_data.get("fields")
            if api_fields:
                field_names_str = f"fields={field_names}"
                detail_str = f"field={api_fields}"
                new_msg = f"{exc} ; {detail_str} ; {field_names_str} ; table_id={table_id}"
            else:
                new_msg = f"{exc} ; fields={field_names} ; table_id={table_id}"
        else:
            new_msg = f"{exc} ; fields={field_names} ; table_id={table_id}"
    else:
        new_msg = f"{exc} ; fields={field_names} ; table_id={table_id}"
    return ConnectorOperationError(
        backend=exc.backend,
        operation=exc.operation,
        kind=exc.kind,
        source_name=exc.source_name,
        retry_delay_s=exc.retry_delay_s,
        cause=exc.__cause__ or exc,
        message=new_msg,
    )


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


def _classify_status(status: int) -> ConnectorErrorKind:
    if status == 429:
        return ConnectorErrorKind.THROTTLED
    if status >= 500:
        return ConnectorErrorKind.TRANSIENT
    if status in {401, 403, 404}:
        return ConnectorErrorKind.MISCONFIGURED
    return ConnectorErrorKind.PERMANENT


def _classify_api_error(*, status: int, code: int | None, message: str) -> ConnectorErrorKind:
    if status == 429:
        return ConnectorErrorKind.THROTTLED
    if status >= 500:
        return ConnectorErrorKind.TRANSIENT
    lowered = message.lower()
    if any(token in lowered for token in ("rate", "too many", "too frequent", "qps", "limit")):
        return ConnectorErrorKind.THROTTLED
    if any(token in lowered for token in ("auth", "token", "permission", "forbidden", "scope", "tenant")):
        return ConnectorErrorKind.MISCONFIGURED
    if any(token in lowered for token in ("not found", "app", "table")) and status in {400, 401, 403, 404}:
        return ConnectorErrorKind.MISCONFIGURED
    if any(token in lowered for token in ("field", "filter", "invalid", "bad request")):
        return ConnectorErrorKind.PERMANENT
    if code in {99991663, 99991664, 99991665}:
        return ConnectorErrorKind.THROTTLED
    return _classify_status(status)


def _is_search_shape_error(exc: ConnectorOperationError) -> bool:
    cause = exc.cause
    if not isinstance(cause, FeishuBitableApiError):
        return False
    message = str(cause).lower()
    return any(
        token in message
        for token in (
            "field validation failed",
            "invalidsort",
            "invalid sort",
            "invalidfilter",
            "invalid filter",
        )
    )


def _quote_path(value: str) -> str:
    return urllib.parse.quote(_require_non_empty_string(value, field="path value"), safe="")


def _redact_token(value: str) -> str:
    return _REDACTED if value else ""


def _short_token(value: str) -> str:
    normalized = _require_non_empty_string(value, field="app_token")
    if len(normalized) <= 8:
        return normalized
    return f"{normalized[:4]}...{normalized[-4:]}"


def _redact_url(value: str) -> str:
    parsed = urlsplit(value)
    netloc = parsed.netloc
    if "@" in netloc:
        _, host = netloc.rsplit("@", 1)
        netloc = f"{_REDACTED}@{host}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
