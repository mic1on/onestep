from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from onestep.envelope import Envelope
from onestep.connectors.base import Delivery, Source
from onestep.resilience import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError
from onestep.state import CursorStore

from ._shared import (
    FeishuBitablePayloadError,
    _cursor_after,
    _cursor_sort_key,
    _incremental_search_body,
    _is_search_shape_error,
    _normalize_batch_size,
    _normalize_fallback_scan_page_limit,
    _normalize_poll_interval,
    _normalize_user_id_type,
    _record_cursor_value,
    _record_fields,
    _record_id,
    _redact_token,
    _redact_url,
    _require_non_empty_string,
)

if TYPE_CHECKING:
    from .connector import FeishuBitableConnector

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

    async def release_unstarted(self) -> None:
        # The runtime dropped this batch before it started processing; the token must
        # not sit at the head of the pending deque because that would block every
        # later prefix commit forever. do not commit it: the records were never
        # delivered, so the durable cursor must not advance past them.
        await self._source.discard_token(self._token)

    async def retry(self, *, delay_s: float | None = None) -> None:
        if delay_s:
            await asyncio.sleep(delay_s)
        # A retry must actually be re-read, so this cannot simply drop the token:
        # ``fetch()`` had already advanced ``_fetched_cursor`` to the batch tail, and
        # the read cursor is what the next poll searches from. Dropping alone would
        # leave the record invisible until the process restarts. Rewind the read
        # cursor to just before this token and drop it from the pending deque so the
        # prefix commit is not wedged. The token is never committed here.
        await self._source.requeue_token(self._token)

    async def fail(self, exc: Exception | None = None) -> None:
        # The record is permanently abandoned. Drop the token so the prefix commit can
        # advance past it and the durable cursor does not freeze forever; this is a
        # deliberate data-loss trade-off chosen over a permanent cursor stall. The
        # cursor is not written here: ack_token() still only moves it over a contiguous
        # run of acked prefixes.
        await self._source.discard_token(self._token)


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

    async def discard_token(self, token: _FeishuCursorToken) -> None:
        """Drop ``token`` from the pending deque without committing it.

        Used by the delivery's release/retry/fail paths so that an abandoned token can
        never wedge the prefix commit. Idempotent and safe when the token is unknown.
        """
        lock = self._commit_lock
        if lock is None:
            lock = asyncio.Lock()
            self._commit_lock = lock
        async with lock:
            value = token.value
            try:
                self._pending.remove(value)
            except ValueError:
                return
            self._acked.discard(value)
            # Removing a head token can expose a run of already-acked successors.
            # Drain it through the normal prefix-commit path so the durable cursor
            # moves as far as the acks allow instead of waiting for a later ack.
            advanced: tuple[Any, str] | None = None
            while self._pending and self._pending[0] in self._acked:
                advanced = self._pending.popleft()
                self._acked.remove(advanced)
            if advanced is not None:
                self._committed_cursor = advanced
                if not self._pending:
                    self._fetched_cursor = advanced
                await self.state.save(self.state_key, [advanced[0], advanced[1]])

    async def requeue_token(self, token: _FeishuCursorToken) -> None:
        """Drop ``token`` without committing it and rewind the read cursor onto it.

        ``fetch()`` advances ``_fetched_cursor`` to the tail of the batch it returned,
        and reads start from ``_fetched_cursor or _committed_cursor``. A retried record
        must therefore be visible again on the next poll: rewinding the read cursor to
        the token immediately *before* this one makes the next search re-include it
        (``_cursor_after`` keeps strictly-greater records, so pointing at the token
        itself would skip it).

        The durable cursor is never moved onto ``token``; only an already-acked
        contiguous prefix may commit. Idempotent and safe when the token is unknown.
        """
        await self.discard_token(token)
        lock = self._commit_lock
        if lock is None:
            lock = asyncio.Lock()
            self._commit_lock = lock
        async with lock:
            value = token.value
            # Never rewind behind the durable cursor: those rows were already committed
            # and re-reading them would replay the whole range.
            if self._committed_cursor is not None and not _cursor_after(value, self._committed_cursor):
                return
            if self._fetched_cursor is None or not _cursor_after(self._fetched_cursor, value):
                return
            # Newest already-pending token strictly below the retried one; the search
            # is exclusive, so pointing here re-includes the retried record.
            predecessor: tuple[Any, str] | None = self._committed_cursor
            for pending in self._pending:
                if _cursor_after(value, pending) and (
                    predecessor is None or _cursor_after(pending, predecessor)
                ):
                    predecessor = pending
            if predecessor is not None:
                self._fetched_cursor = predecessor

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
        seen_page_tokens: set[str] = set()
        # One shared page bound for both the sorted main path and the unsorted fallback.
        # Without it the main path could page forever: it only stops early once it has
        # collected ``limit`` records, so when every returned row is already behind the
        # read cursor (a lagging cursor over a large table) it never fills and never
        # stops, hammering the API until ``has_more`` happens to go false.
        max_pages = self.fallback_scan_page_limit
        while True:
            if pages_scanned >= max_pages:
                raise ConnectorOperationError(
                    backend="feishu_bitable",
                    operation=ConnectorOperation.FETCH,
                    kind=ConnectorErrorKind.PERMANENT,
                    source_name=self.name,
                    retry_delay_s=self.poll_interval_s,
                    message=(
                        "feishu_bitable incremental scan exceeded "
                        f"fallback_scan_page_limit={self.fallback_scan_page_limit} pages "
                        f"for cursor field {self.cursor_field!r}; "
                        "make the cursor field sortable or increase fallback_scan_page_limit"
                    ),
                )
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
            if not has_more:
                break
            if not page_token:
                # Truncating here would let the caller advance the durable cursor past
                # pages it never read, silently losing those rows. Fail loudly instead.
                raise ConnectorOperationError(
                    backend="feishu_bitable",
                    operation=ConnectorOperation.FETCH,
                    kind=ConnectorErrorKind.TRANSIENT,
                    source_name=self.name,
                    retry_delay_s=self.poll_interval_s,
                    message=(
                        "feishu_bitable search reported has_more=true without a page_token; "
                        "refusing to advance the cursor past unread pages"
                    ),
                )
            if page_token in seen_page_tokens:
                # A repeated token means the server is not advancing; looping would
                # re-read the same page forever.
                raise ConnectorOperationError(
                    backend="feishu_bitable",
                    operation=ConnectorOperation.FETCH,
                    kind=ConnectorErrorKind.TRANSIENT,
                    source_name=self.name,
                    retry_delay_s=self.poll_interval_s,
                    message=(
                        "feishu_bitable search returned a repeated page_token; "
                        "aborting to avoid re-reading the same page"
                    ),
                )
            seen_page_tokens.add(page_token)
            if not scan_all_pages and len(records) >= limit:
                break
        records.sort(key=lambda item: _cursor_sort_key((_record_cursor_value(item, self.cursor_field), _record_id(item))))
        return records[:limit]
