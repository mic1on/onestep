from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from onestep.envelope import Envelope
from onestep.resilience import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError
from onestep.state import CursorStore, InMemoryCursorStore

from .base import Delivery, Sink, Source

_DEFAULT_BASE_URL = "https://open.feishu.cn"
_DEFAULT_TIMEOUT_S = 10.0
_DEFAULT_BATCH_SIZE = 100
_MAX_PAGE_SIZE = 500
_TOKEN_REFRESH_MARGIN_S = 60.0
_REDACTED = "<redacted>"
_AUTOMATIC_CURSOR_FIELD_ALIASES = {
    "创建时间": "created_time",
    "最后修改时间": "last_modified_time",
    "最后更新时间": "last_modified_time",
}
_USER_ID_TYPES = frozenset({"open_id", "union_id", "user_id"})


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
        match_field: str | None = None,
        user_id_type: str | None = None,
    ) -> "FeishuBitableTableSink":
        return FeishuBitableTableSink(
            connector=self,
            app_token=app_token,
            table_id=table_id,
            mode=mode,
            match_field=match_field,
            user_id_type=user_id_type,
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
        path = _bitable_records_path(
            app_token=app_token,
            table_id=table_id,
            suffix=f"/{_quote_path(record_id)}",
        )
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
            if not has_more or not page_token:
                break
            if not scan_all_pages and len(records) >= limit:
                break
        records.sort(key=lambda item: _cursor_sort_key((_record_cursor_value(item, self.cursor_field), _record_id(item))))
        return records[:limit]


class FeishuBitableTableSink(Sink):
    def __init__(
        self,
        *,
        connector: FeishuBitableConnector,
        app_token: str,
        table_id: str,
        mode: str,
        match_field: str | None,
        user_id_type: str | None,
    ) -> None:
        super().__init__(f"feishu_bitable.table_sink:{table_id}")
        normalized_mode = _normalize_mode(mode)
        if normalized_mode in {"upsert", "update"}:
            match_field = _require_non_empty_string(match_field, field="match_field")
        elif match_field is not None:
            match_field = _require_non_empty_string(match_field, field="match_field")
        self.connector = connector
        self.app_token = _require_non_empty_string(app_token, field="app_token")
        self.table_id = _require_non_empty_string(table_id, field="table_id")
        self.mode = normalized_mode
        self.match_field = match_field
        self.user_id_type = _normalize_user_id_type(user_id_type)

    async def send(self, envelope: Envelope) -> None:
        try:
            fields = _payload_fields(envelope.body)
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

            match_field = self.match_field or ""
            match_value = fields.get(match_field)
            if _empty_match_value(match_value):
                raise FeishuBitablePayloadError(f"payload must include non-empty match field {match_field!r}")
            matches = await self._find_matches(match_field, match_value)
            if len(matches) > 1:
                raise FeishuBitablePayloadError(
                    f"upsert match field {match_field!r} matched {len(matches)} records"
                )
            if matches:
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
                raise FeishuBitablePayloadError(f"no record matched field {match_field!r}")
            await self.connector.create_record(
                app_token=self.app_token,
                table_id=self.table_id,
                fields=fields,
                user_id_type=self.user_id_type,
                operation=ConnectorOperation.SEND,
                source_name=self.name,
                retry_delay_s=1.0,
            )
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
            ) from exc

    def control_plane_descriptor(self) -> dict[str, Any]:
        return {
            "kind": "feishu_bitable_table_sink",
            "name": self.name,
            "config": {
                "base_url": _redact_url(self.connector.base_url),
                "app_token": _redact_token(self.app_token),
                "table_id": self.table_id,
                "mode": self.mode,
                "match_field": self.match_field,
                "user_id_type": self.user_id_type,
            },
        }

    async def _find_matches(self, match_field: str, match_value: Any) -> list[dict[str, Any]]:
        data = await self.connector.search_records(
            app_token=self.app_token,
            table_id=self.table_id,
            body=_match_search_body(match_field, match_value),
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


def _match_search_body(match_field: str, match_value: Any) -> dict[str, Any]:
    return {
        "filter": {
            "conjunction": "and",
            "conditions": [
                {
                    "field_name": match_field,
                    "operator": "is",
                    "value": [match_value],
                }
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


def _normalize_mode(value: str) -> str:
    normalized = _require_non_empty_string(value, field="mode").strip().lower()
    if normalized not in {"upsert", "create", "update"}:
        raise ValueError("mode must be one of 'upsert', 'create', or 'update'")
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


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
