from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

from onestep.resilience import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError
from onestep.state import CursorStore, InMemoryCursorStore

from ._shared import (
    DEFAULT_FALLBACK_SCAN_PAGE_LIMIT,
    FeishuBitableApiError,
    _DEFAULT_AMBIGUOUS_WRITE_MAX_ROUNDS,
    _DEFAULT_BASE_URL,
    _DEFAULT_BATCH_SIZE,
    _DEFAULT_INSERT_INDEX_MAX_PAGES,
    _DEFAULT_INSERT_INDEX_PAGE_SIZE,
    _DEFAULT_TIMEOUT_S,
    _LOGGER_NAME,
    _TOKEN_REFRESH_MARGIN_S,
    _bitable_records_path,
    _check_field_values_before_send,
    _classify_api_error,
    _classify_status,
    _classify_transport_error,
    _default_incremental_state_key,
    _normalize_base_url,
    _normalize_page_size,
    _normalize_timeout,
    _normalize_user_id_type,
    _optional_int,
    _quote_path,
    _redact_token,
    _require_non_empty_string,
    _user_id_type_query,
    _with_field_context,
)
from .sink import FeishuBitableTableSink
from .source import FeishuBitableIncrementalSource

logger = logging.getLogger(_LOGGER_NAME)


def _redact_request_path(path: str) -> str:
    """Redact the app token inside a records path for request logs.

    Keeps ``table_id`` and the ``records`` suffix so request logs still show
    which table and endpoint a call hit, without leaking the app token.
    """
    parts = path.split("/")
    # /bitable/v1/apps/{app_token}/tables/{table_id}/records{suffix}
    if len(parts) > 4 and parts[1] == "bitable" and parts[3] == "apps":
        parts[4] = _redact_token(parts[4])
    return "/".join(parts)

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
        insert_key_index: bool = False,
        insert_index_page_size: int = _DEFAULT_INSERT_INDEX_PAGE_SIZE,
        insert_index_max_pages: int = _DEFAULT_INSERT_INDEX_MAX_PAGES,
        ambiguous_write_max_rounds: int = _DEFAULT_AMBIGUOUS_WRITE_MAX_ROUNDS,
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
            insert_key_index=insert_key_index,
            insert_index_page_size=insert_index_page_size,
            insert_index_max_pages=insert_index_max_pages,
            ambiguous_write_max_rounds=ambiguous_write_max_rounds,
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
        start_time = time.monotonic()
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
        logger.debug(
            "feishu api request",
            extra={
                "event": "feishu_api_request",
                "method": method.upper(),
                "path": _redact_request_path(path),
                "operation": operation.value,
                "source_name": source_name,
                "status": status,
                "code": code,
                "duration_ms": round((time.monotonic() - start_time) * 1000, 1),
            },
        )
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

