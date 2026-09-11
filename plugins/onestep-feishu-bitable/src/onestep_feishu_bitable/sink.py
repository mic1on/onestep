from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from onestep.envelope import Envelope
from onestep.connectors.base import Sink
from onestep.resilience import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError

from ._shared import (
    FeishuBitablePayloadError,
    _DEFAULT_AMBIGUOUS_WRITE_MAX_ROUNDS,
    _DEFAULT_INSERT_INDEX_MAX_PAGES,
    _DEFAULT_INSERT_INDEX_PAGE_SIZE,
    _FeishuRelationConfig,
    _LOGGER_NAME,
    _MAX_PAGE_SIZE,
    _batch_create_response_is_complete,
    _canonical_insert_key,
    _empty_match_value,
    _is_definite_write_error,
    _match_search_body,
    _match_values,
    _normalize_ambiguous_write_max_rounds,
    _normalize_insert_index_max_pages,
    _normalize_insert_index_page_size,
    _normalize_insert_key_index,
    _normalize_match_fields,
    _normalize_mode,
    _normalize_relation_values,
    _normalize_relations,
    _normalize_user_id_type,
    _payload_fields,
    _record_id,
    _redact_token,
    _redact_url,
    _require_non_empty_string,
    _validate_insert_key_index_requirements,
)

logger = logging.getLogger(_LOGGER_NAME)

if TYPE_CHECKING:
    from .connector import FeishuBitableConnector


def _scan_total(data: Mapping[str, Any]) -> int | None:
    """Read Feishu's response ``total``, tolerating anything unexpected.

    ``total`` is metadata only: a missing or malformed value must never fail a
    scan, so it degrades to ``None`` instead of raising.
    """
    total = data.get("total")
    if isinstance(total, bool) or not isinstance(total, int):
        return None
    return total


def _redacted_scan_error(exc: BaseException, target_field: str) -> str:
    """Summarize a scan failure without leaking business data or credentials.

    Only the exception type and the relation's target field are reported; the
    exception text is withheld because it may embed record ids or tokens.
    """
    return f"{type(exc).__name__} while scanning relation field {target_field!r}"

@dataclass
class _FeishuRelationCreateLock:
    lock: asyncio.Lock
    users: int = 0
    record_id: str | None = None


class _InsertState(str, Enum):
    BUFFERED = "buffered"
    WRITING = "writing"
    RECOVERING = "recovering"


@dataclass
class _PendingInsert:
    key: str
    fields: dict[str, Any]
    waiters: list[asyncio.Future[None]] = field(default_factory=list)
    buffered_at: float = 0.0
    state: _InsertState = _InsertState.BUFFERED


class FeishuBitableTableSink(Sink):
    """Feishu Bitable table sink with optional batch buffering for create mode.

    When ``mode="create"`` and ``batch_size > 1``, records are buffered and
    flushed in batches via the Feishu ``batch_create`` API.  This dramatically
    reduces API calls — 500 records become 1 call instead of 500.

    upsert and update modes still process records one at a time because
    each record requires a match-finding search before the write.

    Opt-in insert_key_index mode preloads the destination match field into
    memory so normal Insert processing requires zero per-key Feishu searches.
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
        insert_key_index: bool = False,
        insert_index_page_size: int = _DEFAULT_INSERT_INDEX_PAGE_SIZE,
        insert_index_max_pages: int = _DEFAULT_INSERT_INDEX_MAX_PAGES,
        ambiguous_write_max_rounds: int = _DEFAULT_AMBIGUOUS_WRITE_MAX_ROUNDS,
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
        # Process-wide (per-sink) relation caches: {target_field: {business_key: record_id}}.
        # Only relations with cache != "none" get an entry; the cache is a pure
        # acceleration layer -- Feishu stays the source of truth and every miss
        # still falls back to a search.
        self._relation_caches: dict[str, dict[str, str]] = {
            relation.target_field: {} for relation in self.relations if relation.cache != "none"
        }
        self._relation_eager_loaded: set[str] = set()
        self._batch_size = max(1, min(batch_size, _MAX_PAGE_SIZE))
        self._flush_interval_s = float(flush_interval_s)
        # Insert key index configuration
        self.insert_key_index = _normalize_insert_key_index(insert_key_index)
        self.insert_index_page_size = _normalize_insert_index_page_size(insert_index_page_size)
        self.insert_index_max_pages = _normalize_insert_index_max_pages(insert_index_max_pages)
        self.ambiguous_write_max_rounds = _normalize_ambiguous_write_max_rounds(ambiguous_write_max_rounds)
        if self.insert_key_index:
            _validate_insert_key_index_requirements(
                mode=self.mode,
                match_fields=self.match_fields,
            )
        # Buffer stores raw payload fields (before relation resolution & match-finding)
        self._buffer: list[dict[str, Any]] = []
        self._buffer_lock: asyncio.Lock | None = None
        self._flush_task: asyncio.Task[None] | None = None
        self._flush_error: Exception | None = None
        self._closed = False
        # Insert key index state
        self._insert_keys: set[str] | None = None
        self._index_loaded = False
        self._scan_duplicate_keys = 0
        self._scan_missing_key_records = 0
        # Indexed insert pending-key tracking
        self._pending_by_key: dict[str, _PendingInsert] = {}
        self._pending_order: deque[str] = deque()
        self._flush_lock: asyncio.Lock | None = None
        self._uncertain_keys: set[str] = set()
        self._normal_lookup_avoided_count = 0
        self._recovery_lookup_count = 0
        self._insert_retry_count = 0
        self._inflight_waiter_count = 0

    async def open(self) -> None:
        """Open the sink and preload any eager indexes.

        ``insert_key_index`` and ``relations`` are mutually exclusive, so the
        destination-key scan and the relation scans never run in the same sink.
        """
        if self.insert_key_index and not self._index_loaded:
            await self._load_insert_key_index()
        await self._load_relation_eager_caches()

    def _relation_cached_id(self, relation: _FeishuRelationConfig, value: str) -> str | None:
        """Return the cached record_id for a business key, or None when absent.

        A miss is never treated as "does not exist in Feishu": callers must fall
        back to a search so ``on_missing: create`` cannot fabricate duplicates.
        """
        cache = self._relation_caches.get(relation.target_field)
        if cache is None:
            return None
        return cache.get(value)

    def _relation_cache_store(
        self, relation: _FeishuRelationConfig, value: str, record_id: str
    ) -> None:
        """Cache a confirmed unique record_id for a business key."""
        cache = self._relation_caches.get(relation.target_field)
        if cache is not None:
            cache[value] = record_id

    async def _load_insert_key_index(self) -> None:
        """Page the configured match field into a bounded in-memory set.

        The scan requests only the match field, stops at insert_index_max_pages,
        and fails closed if the destination table has more records than the bound.
        """
        page_token: str | None = None
        seen_tokens: set[str] = set()
        loaded: set[str] = set()
        missing_key_records = 0
        duplicate_keys = 0
        start_time = time.monotonic()

        for page_number in range(1, self.insert_index_max_pages + 1):
            try:
                data = await self.connector.search_records(
                    app_token=self.app_token,
                    table_id=self.table_id,
                    body={"field_names": [self.match_fields[0]]},
                    page_size=self.insert_index_page_size,
                    page_token=page_token,
                    user_id_type=self.user_id_type,
                    operation=ConnectorOperation.OPEN,
                    source_name=self.name,
                    retry_delay_s=1.0,
                )
            except ConnectorOperationError:
                raise
            except Exception as exc:
                raise ConnectorOperationError(
                    backend="feishu_bitable",
                    operation=ConnectorOperation.OPEN,
                    kind=ConnectorErrorKind.PERMANENT,
                    source_name=self.name,
                    retry_delay_s=1.0,
                    cause=exc,
                    message="feishu_bitable insert index scan failed",
                ) from exc

            raw_items = data.get("items", [])
            if not isinstance(raw_items, list):
                raise ConnectorOperationError(
                    backend="feishu_bitable",
                    operation=ConnectorOperation.OPEN,
                    kind=ConnectorErrorKind.PERMANENT,
                    source_name=self.name,
                    retry_delay_s=1.0,
                    message="feishu_bitable insert index response items must be a list",
                )

            for raw_item in raw_items:
                if not isinstance(raw_item, Mapping):
                    missing_key_records += 1
                    continue
                raw_fields = raw_item.get("fields")
                if not isinstance(raw_fields, Mapping):
                    missing_key_records += 1
                    continue
                try:
                    key = _canonical_insert_key(raw_fields.get(self.match_fields[0]))
                except FeishuBitablePayloadError:
                    missing_key_records += 1
                    continue
                if key in loaded:
                    duplicate_keys += 1
                loaded.add(key)

            has_more = bool(data.get("has_more"))
            next_token = data.get("page_token")
            if not has_more:
                self._insert_keys = loaded
                self._index_loaded = True
                self._scan_duplicate_keys = duplicate_keys
                self._scan_missing_key_records = missing_key_records
                duration = time.monotonic() - start_time
                logger.info(
                    "feishu insert index scan",
                    extra={
                        "event": "feishu_insert_index_scan",
                        "scan_pages": page_number,
                        "scan_keys": len(loaded),
                        "missing_key_records": missing_key_records,
                        "duplicate_keys": duplicate_keys,
                        "duration_s": round(duration, 3),
                        "outcome": "success",
                        "page_size": self.insert_index_page_size,
                        "max_pages": self.insert_index_max_pages,
                    },
                )
                return

            if not isinstance(next_token, str) or not next_token or next_token in seen_tokens:
                raise ConnectorOperationError(
                    backend="feishu_bitable",
                    operation=ConnectorOperation.OPEN,
                    kind=ConnectorErrorKind.PERMANENT,
                    source_name=self.name,
                    retry_delay_s=1.0,
                    message="feishu_bitable insert index pagination did not advance",
                )
            seen_tokens.add(next_token)
            page_token = next_token

        # Exhausted max_pages while Feishu still has more
        raise ConnectorOperationError(
            backend="feishu_bitable",
            operation=ConnectorOperation.OPEN,
            kind=ConnectorErrorKind.PERMANENT,
            source_name=self.name,
            retry_delay_s=1.0,
            message=(
                "feishu_bitable insert index exceeded "
                f"insert_index_max_pages={self.insert_index_max_pages}"
            ),
        )

    async def _load_relation_eager_caches(self) -> None:
        """Preload every ``cache: eager`` relation's key field into memory."""
        for relation in self.relations:
            if relation.cache != "eager" or relation.target_field in self._relation_eager_loaded:
                continue
            await self._load_relation_eager_cache(relation)

    async def _load_relation_eager_cache(self, relation: _FeishuRelationConfig) -> None:
        """Page a relation table's key field into an in-memory {key: record_id} map.

        Mirrors ``_load_insert_key_index``: bounded paging, token anti-repeat, and
        fail-closed startup when the bound is exhausted. A truncated cache would
        turn almost every key into a runtime miss, so it is refused outright.

        Progress is observable through ``feishu_relation_cache_scan`` logs, one per
        phase: ``start`` (once), ``page`` (every page), ``done`` or ``error``.
        Every line stays redacted: only counts and table metadata, never business
        keys, record ids, or app tokens.
        """
        page_token: str | None = None
        seen_tokens: set[str] = set()
        loaded: dict[str, str] = {}
        missing_key_records = 0
        duplicate_keys = 0
        multi_key_records = 0
        start_time = time.monotonic()

        def log_scan(phase: str, *, detail: str | None = None, **fields: Any) -> None:
            extra: dict[str, Any] = {
                "event": "feishu_relation_cache_scan",
                "phase": phase,
                "target_field": relation.target_field,
                "table_id": relation.table_id,
            }
            if detail is not None:
                extra["error"] = detail
            extra.update(fields)
            logger.info("feishu relation cache scan", extra=extra)

        log_scan(
            "start",
            page_size=self.insert_index_page_size,
            max_pages=self.insert_index_max_pages,
        )

        for page_number in range(1, self.insert_index_max_pages + 1):
            try:
                data = await self.connector.search_records(
                    app_token=relation.app_token,
                    table_id=relation.table_id,
                    body={"field_names": [relation.key]},
                    page_size=self.insert_index_page_size,
                    page_token=page_token,
                    user_id_type=self.user_id_type,
                    operation=ConnectorOperation.OPEN,
                    source_name=self.name,
                    retry_delay_s=1.0,
                )
            except ConnectorOperationError as exc:
                log_scan("error", detail=_redacted_scan_error(exc, relation.target_field))
                raise
            except Exception as exc:
                log_scan("error", detail=_redacted_scan_error(exc, relation.target_field))
                raise ConnectorOperationError(
                    backend="feishu_bitable",
                    operation=ConnectorOperation.OPEN,
                    kind=ConnectorErrorKind.PERMANENT,
                    source_name=self.name,
                    retry_delay_s=1.0,
                    cause=exc,
                    message=(
                        "feishu_bitable relation cache scan failed for "
                        f"relation field {relation.target_field!r}"
                    ),
                ) from exc

            raw_items = data.get("items", [])
            if not isinstance(raw_items, list):
                detail = "feishu_bitable relation cache response items must be a list"
                log_scan("error", detail=detail)
                raise ConnectorOperationError(
                    backend="feishu_bitable",
                    operation=ConnectorOperation.OPEN,
                    kind=ConnectorErrorKind.PERMANENT,
                    source_name=self.name,
                    retry_delay_s=1.0,
                    message=detail,
                )

            for raw_item in raw_items:
                if not isinstance(raw_item, Mapping):
                    missing_key_records += 1
                    continue
                raw_fields = raw_item.get("fields")
                if not isinstance(raw_fields, Mapping):
                    missing_key_records += 1
                    continue
                try:
                    keys = _normalize_relation_values(
                        raw_fields.get(relation.key), field=relation.key
                    )
                except FeishuBitablePayloadError:
                    # Tolerate one malformed cell instead of failing the whole scan.
                    missing_key_records += 1
                    continue
                if not keys:
                    missing_key_records += 1
                    continue
                if len(keys) > 1:
                    multi_key_records += 1
                record_id = _record_id(raw_item)
                for business_key in keys:
                    if business_key in loaded:
                        duplicate_keys += 1
                    loaded[business_key] = record_id

            has_more = bool(data.get("has_more"))
            next_token = data.get("page_token")
            log_scan(
                "page",
                page_number=page_number,
                page_records=len(raw_items),
                scan_keys=len(loaded),
                missing_key_records=missing_key_records,
                duplicate_keys=duplicate_keys,
                total=_scan_total(data),
                has_more=has_more,
            )
            if not has_more:
                self._relation_caches[relation.target_field] = loaded
                self._relation_eager_loaded.add(relation.target_field)
                duration = time.monotonic() - start_time
                log_scan(
                    "done",
                    scan_pages=page_number,
                    scan_keys=len(loaded),
                    missing_key_records=missing_key_records,
                    duplicate_keys=duplicate_keys,
                    multi_key_records=multi_key_records,
                    duration_s=round(duration, 3),
                    outcome="success",
                    total=_scan_total(data),
                    page_size=self.insert_index_page_size,
                    max_pages=self.insert_index_max_pages,
                )
                return

            if not isinstance(next_token, str) or not next_token or next_token in seen_tokens:
                detail = (
                    "feishu_bitable relation cache pagination did not advance for "
                    f"relation field {relation.target_field!r}"
                )
                log_scan("error", detail=detail)
                raise ConnectorOperationError(
                    backend="feishu_bitable",
                    operation=ConnectorOperation.OPEN,
                    kind=ConnectorErrorKind.PERMANENT,
                    source_name=self.name,
                    retry_delay_s=1.0,
                    message=detail,
                )
            seen_tokens.add(next_token)
            page_token = next_token

        # Exhausted max_pages while Feishu still has more: refuse the truncated cache.
        detail = (
            f"feishu_bitable relation cache for field {relation.target_field!r} exceeded "
            f"insert_index_max_pages={self.insert_index_max_pages}"
        )
        log_scan("error", detail=detail)
        raise ConnectorOperationError(
            backend="feishu_bitable",
            operation=ConnectorOperation.OPEN,
            kind=ConnectorErrorKind.PERMANENT,
            source_name=self.name,
            retry_delay_s=1.0,
            message=detail,
        )

    def _ensure_buffer_lock(self) -> asyncio.Lock:
        if self._buffer_lock is None:
            self._buffer_lock = asyncio.Lock()
        return self._buffer_lock

    async def send(self, envelope: Envelope) -> None:
        """Buffer raw fields for batch processing."""
        try:
            raw_fields = _payload_fields(envelope.body)
            # Indexed insert path
            if self.insert_key_index and self._index_loaded:
                await self._send_indexed_insert(raw_fields)
            else:
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
        """Preserve the existing buffered behavior for non-indexed sinks."""
        if self._batch_size <= 1:
            await self._send_single(raw_fields)
            return
        lock = self._ensure_buffer_lock()
        async with lock:
            if self._flush_error is not None:
                err = self._flush_error
                self._flush_error = None
                raise err
            self._buffer.append(raw_fields)
            if len(self._buffer) >= self._batch_size:
                await self._flush_buffer()
                return
            if self._flush_task is None and not self._closed:
                self._flush_task = asyncio.create_task(self._flush_after_interval())

    async def _send_indexed_insert(self, raw_fields: dict[str, Any]) -> None:
        """Send one record through the indexed insert path with per-key waiter.

        If the key is already in the startup index, return immediately.
        Otherwise buffer the record, join any existing waiter for the same key,
        and await the batch outcome.
        """
        if self._closed:
            raise ConnectorOperationError(
                backend="feishu_bitable",
                operation=ConnectorOperation.SEND,
                kind=ConnectorErrorKind.PERMANENT,
                source_name=self.name,
                retry_delay_s=1.0,
                message="feishu_bitable sink is closed",
            )

        # Parse and canonicalize the match key
        match_value = raw_fields.get(self.match_fields[0])
        if _empty_match_value(match_value):
            raise FeishuBitablePayloadError(
                f"payload must include non-empty match_fields entry {self.match_fields[0]!r}"
            )
        key = _canonical_insert_key(match_value)

        # Key is in the startup index: confirmed pre-existing
        if self._insert_keys is not None and key in self._insert_keys:
            self._normal_lookup_avoided_count += 1
            return

        # Key is uncertain from a previous ambiguous write: reconcile first
        if key in self._uncertain_keys:
            await self._reconcile_uncertain_key(key)
            if self._insert_keys is not None and key in self._insert_keys:
                self._normal_lookup_avoided_count += 1
                return

        # Resolve relation fields before buffering so the created record carries
        # record ids (not business values). Reuses the relation cache: eager/lazy
        # hits resolve in-memory with zero search.
        if self.relations:
            raw_fields = await self._resolve_relation_fields(raw_fields)

        # Buffer with a waiter.  The pending entry stays in the map while its
        # batch is being written so a concurrent duplicate joins the same
        # outcome instead of creating a second record.
        lock = self._ensure_buffer_lock()
        async with lock:
            if self._flush_error is not None:
                err = self._flush_error
                self._flush_error = None
                raise err

            waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            existing = self._pending_by_key.get(key)
            if existing is not None:
                existing.waiters.append(waiter)
            else:
                pending = _PendingInsert(
                    key=key,
                    fields=raw_fields,
                    waiters=[waiter],
                    buffered_at=time.monotonic(),
                )
                self._pending_by_key[key] = pending
                self._pending_order.append(key)
            self._inflight_waiter_count += 1
            should_flush = len(self._pending_order) >= self._batch_size
            if not should_flush and self._flush_task is None and not self._closed:
                self._flush_task = asyncio.create_task(self._flush_after_interval())

        if should_flush:
            await self._flush_indexed_insert(reason="threshold")

        # Await the waiter (may be resolved during flush above, or by timer/close)
        try:
            await waiter
        except asyncio.CancelledError:
            # Cancellation cancels this caller's Future, but the key group must
            # remain until its batch is confirmed or failed.  Close and the
            # elected flusher therefore still settle the group.
            raise

    def _seal_indexed_batch(self, batch_size: int) -> list[_PendingInsert]:
        """Seal a batch from _pending_order at most batch_size unique keys."""
        batch: list[_PendingInsert] = []
        seen: int = 0
        while self._pending_order and seen < batch_size:
            key = self._pending_order[0]
            pending = self._pending_by_key.get(key)
            if pending is None:
                self._pending_order.popleft()
                continue
            if pending.state != _InsertState.BUFFERED:
                self._pending_order.popleft()
                continue
            pending.state = _InsertState.WRITING
            batch.append(pending)
            self._pending_order.popleft()
            seen += 1
        return batch

    def _ensure_flush_lock(self) -> asyncio.Lock:
        if self._flush_lock is None:
            self._flush_lock = asyncio.Lock()
        return self._flush_lock

    async def _write_indexed_batch(
        self,
        batch: list[_PendingInsert],
        *,
        reason: str,
        recovery_round: int = 0,
    ) -> None:
        """Write one indexed insert batch and settle every member."""
        if not batch:
            return

        batch_size = len(batch)
        start_time = time.monotonic()
        batch_records = [dict(pending.fields) for pending in batch]

        try:
            result = await self.connector.batch_create_records(
                app_token=self.app_token,
                table_id=self.table_id,
                records=batch_records,
                user_id_type=self.user_id_type,
                operation=ConnectorOperation.SEND,
                source_name=self.name,
                retry_delay_s=1.0,
            )
        except ConnectorOperationError as exc:
            duration = time.monotonic() - start_time
            if _is_definite_write_error(exc):
                self._fail_pending(batch, exc)
                logger.info(
                    "feishu insert batch write",
                    extra={
                        "event": "feishu_insert_batch_write",
                        "batch_size": batch_size,
                        "duration_s": round(duration, 3),
                        "outcome": "permanent_error",
                        "flush_reason": reason,
                        "recovery_round": recovery_round,
                        "inflight_waiter_count": self._inflight_waiter_count,
                    },
                )
                return
            await self._recover_ambiguous_batch(
                batch, reason=reason, recovery_round=recovery_round + 1, cause=exc
            )
            return
        except Exception as exc:
            # An unexpected exception after request dispatch has an unknown
            # commit outcome.  Treat it conservatively and reconcile first.
            await self._recover_ambiguous_batch(
                batch,
                reason=reason,
                recovery_round=recovery_round + 1,
                cause=ConnectorOperationError(
                    backend="feishu_bitable",
                    operation=ConnectorOperation.SEND,
                    kind=ConnectorErrorKind.UNCERTAIN,
                    source_name=self.name,
                    retry_delay_s=1.0,
                    cause=exc,
                    message="feishu_bitable batch create outcome is uncertain",
                ),
            )
            return

        if not _batch_create_response_is_complete(result, expected=batch_size):
            await self._recover_ambiguous_batch(
                batch,
                reason=reason,
                recovery_round=recovery_round + 1,
                cause=ConnectorOperationError(
                    backend="feishu_bitable",
                    operation=ConnectorOperation.SEND,
                    kind=ConnectorErrorKind.UNCERTAIN,
                    source_name=self.name,
                    retry_delay_s=1.0,
                    message="feishu_bitable batch create response was incomplete",
                ),
            )
            return

        duration = time.monotonic() - start_time
        self._confirm_created(batch)
        logger.info(
            "feishu insert batch write",
            extra={
                "event": "feishu_insert_batch_write",
                "batch_size": batch_size,
                "duration_s": round(duration, 3),
                "outcome": "success",
                "flush_reason": reason,
                "recovery_round": recovery_round,
                "inflight_waiter_count": self._inflight_waiter_count,
            },
        )

    async def _recover_ambiguous_batch(
        self,
        batch: list[_PendingInsert],
        *,
        reason: str,
        recovery_round: int,
        cause: ConnectorOperationError,
    ) -> None:
        """Reconcile only the keys whose create outcome is uncertain."""
        unresolved = list(batch)
        for pending in unresolved:
            pending.state = _InsertState.RECOVERING
            self._uncertain_keys.add(pending.key)

        logger.info(
            "feishu insert batch write",
            extra={
                "event": "feishu_insert_batch_write",
                "batch_size": len(batch),
                "duration_s": 0.0,
                "outcome": "ambiguous",
                "flush_reason": reason,
                "recovery_round": recovery_round,
                "inflight_waiter_count": self._inflight_waiter_count,
            },
        )

        round_number = recovery_round
        while unresolved and round_number <= self.ambiguous_write_max_rounds:
            self._insert_retry_count += 1
            logger.info(
                "feishu insert retry",
                extra={
                    "event": "feishu_insert_retry",
                    "retry_count": self._insert_retry_count,
                    "recovery_round": round_number,
                    "unresolved_count": len(unresolved),
                },
            )
            found, missing, lookup_failed = await self._lookup_pending_keys(unresolved)
            if found:
                self._confirm_found(found)
            if not missing:
                unresolved = lookup_failed
                round_number += 1
                continue

            for pending in missing:
                pending.state = _InsertState.WRITING
            try:
                result = await self.connector.batch_create_records(
                    app_token=self.app_token,
                    table_id=self.table_id,
                    records=[dict(pending.fields) for pending in missing],
                    user_id_type=self.user_id_type,
                    operation=ConnectorOperation.SEND,
                    source_name=self.name,
                    retry_delay_s=1.0,
                )
            except ConnectorOperationError as exc:
                if _is_definite_write_error(exc):
                    self._fail_pending(missing, exc)
                    unresolved = lookup_failed
                else:
                    unresolved = lookup_failed + missing
            except Exception as exc:
                cause = ConnectorOperationError(
                    backend="feishu_bitable",
                    operation=ConnectorOperation.SEND,
                    kind=ConnectorErrorKind.UNCERTAIN,
                    source_name=self.name,
                    retry_delay_s=1.0,
                    cause=exc,
                    message="feishu_bitable recovery create outcome is uncertain",
                )
                unresolved = lookup_failed + missing
            else:
                if _batch_create_response_is_complete(result, expected=len(missing)):
                    self._confirm_created(missing)
                    unresolved = lookup_failed
                else:
                    unresolved = lookup_failed + missing
            round_number += 1

        if unresolved:
            exhausted = ConnectorOperationError(
                backend="feishu_bitable",
                operation=ConnectorOperation.SEND,
                kind=ConnectorErrorKind.UNCERTAIN,
                source_name=self.name,
                retry_delay_s=1.0,
                cause=cause,
                message=(
                    "feishu_bitable ambiguous batch remained unresolved after "
                    f"{self.ambiguous_write_max_rounds} recovery rounds"
                ),
            )
            self._fail_pending(unresolved, exhausted)

    async def _lookup_pending_keys(
        self, pending_list: list[_PendingInsert]
    ) -> tuple[list[_PendingInsert], list[_PendingInsert], list[_PendingInsert]]:
        """Return found, confirmed-missing, and lookup-failed members."""
        semaphore = asyncio.Semaphore(20)

        async def lookup(
            pending: _PendingInsert,
        ) -> tuple[str, _PendingInsert, BaseException | None]:
            try:
                async with semaphore:
                    matches = await self._find_matches(
                        {self.match_fields[0]: pending.key}
                    )
            except BaseException as exc:
                return "failed", pending, exc
            if len(matches) > 1:
                return (
                    "failed",
                    pending,
                    FeishuBitablePayloadError(
                        "insert match field resolved to multiple destination records"
                    ),
                )
            return ("found" if matches else "missing"), pending, None

        found: list[_PendingInsert] = []
        missing: list[_PendingInsert] = []
        failed: list[_PendingInsert] = []
        results = await asyncio.gather(*(lookup(item) for item in pending_list))
        for outcome, pending, exc in results:
            if outcome == "found":
                found.append(pending)
            elif outcome == "missing":
                missing.append(pending)
            else:
                if isinstance(exc, FeishuBitablePayloadError):
                    self._fail_pending(
                        [pending],
                        ConnectorOperationError(
                            backend="feishu_bitable",
                            operation=ConnectorOperation.SEND,
                            kind=ConnectorErrorKind.PERMANENT,
                            source_name=self.name,
                            retry_delay_s=1.0,
                            cause=exc,
                            message="feishu_bitable destination match is not unique",
                        ),
                    )
                else:
                    failed.append(pending)
        self._recovery_lookup_count += len(pending_list)
        logger.info(
            "feishu insert lookup",
            extra={
                "event": "feishu_insert_lookup",
                "normal_lookup_avoided_count": self._normal_lookup_avoided_count,
                "recovery_lookup_count": self._recovery_lookup_count,
                "outcome": "success" if not failed else "error",
            },
        )
        return found, missing, failed

    def _confirm_created(self, batch: list[_PendingInsert]) -> None:
        """Mark batch keys as created and complete all waiters successfully."""
        for pending in batch:
            if self._insert_keys is not None:
                self._insert_keys.add(pending.key)
            self._uncertain_keys.discard(pending.key)
        self._complete_pending(batch)

    def _confirm_found(self, batch: list[_PendingInsert]) -> None:
        """Mark batch keys as found in the destination and complete waiters."""
        for pending in batch:
            if self._insert_keys is not None:
                self._insert_keys.add(pending.key)
            self._uncertain_keys.discard(pending.key)
        self._complete_pending(batch)

    def _fail_pending(
        self,
        pending_list: list[_PendingInsert],
        exc: BaseException,
    ) -> None:
        """Complete all waiters for the given pending items with an exception."""
        for pending in pending_list:
            self._pending_by_key.pop(pending.key, None)
            self._inflight_waiter_count -= len(pending.waiters)
            for waiter in pending.waiters:
                if not waiter.done():
                    waiter.set_exception(exc)

    def _complete_pending(self, pending_list: list[_PendingInsert]) -> None:
        """Complete all waiters for the given pending items successfully."""
        for pending in pending_list:
            self._pending_by_key.pop(pending.key, None)
            self._inflight_waiter_count -= len(pending.waiters)
            for waiter in pending.waiters:
                if not waiter.done():
                    waiter.set_result(None)

    @property
    def inflight_waiter_count(self) -> int:
        return self._inflight_waiter_count

    async def _reconcile_uncertain_key(self, key: str) -> None:
        """Exact-search one uncertain key before a new send can proceed."""
        try:
            matches = await self._find_matches({self.match_fields[0]: key})
        except ConnectorOperationError:
            self._recovery_lookup_count += 1
            self._log_insert_lookup(outcome="error")
            raise
        self._recovery_lookup_count += 1
        self._log_insert_lookup(outcome="success")
        if len(matches) > 1:
            raise FeishuBitablePayloadError(
                "insert match field resolved to multiple destination records"
            )
        if matches:
            if self._insert_keys is not None:
                self._insert_keys.add(key)
        # A successful exact search establishes either found or missing.
        self._uncertain_keys.discard(key)

    def _log_insert_lookup(self, *, outcome: str) -> None:
        logger.info(
            "feishu insert lookup",
            extra={
                "event": "feishu_insert_lookup",
                "normal_lookup_avoided_count": self._normal_lookup_avoided_count,
                "recovery_lookup_count": self._recovery_lookup_count,
                "outcome": outcome,
            },
        )

    async def _flush_indexed_insert(self, *, reason: str) -> None:
        """Seal and write an indexed insert batch under the flush lock.

        Timer, threshold, and close flushes share one network-write lane.
        """
        async with self._ensure_flush_lock():
            lock = self._ensure_buffer_lock()
            async with lock:
                batch = self._seal_indexed_batch(self._batch_size)
            if not batch:
                return
            logger.info(
                "feishu insert buffer",
                extra={
                    "event": "feishu_insert_buffer",
                    "buffered_batch_size": len(batch),
                    "oldest_batch_age_s": round(
                        max(
                            0.0,
                            time.monotonic()
                            - min(item.buffered_at for item in batch),
                        ),
                        3,
                    ),
                    "inflight_waiter_count": self._inflight_waiter_count,
                    "flush_reason": reason,
                },
            )
            await self._write_indexed_batch(batch, reason=reason)

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
        timer_task = asyncio.current_task()
        try:
            await asyncio.sleep(self._flush_interval_s)
            if self.insert_key_index and self._index_loaded:
                if self._flush_error is None:
                    await self._flush_indexed_insert(reason="timer")
            else:
                lock = self._ensure_buffer_lock()
                async with lock:
                    if self._buffer and self._flush_error is None:
                        await self._flush_buffer()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._flush_error = exc
        finally:
            if self._flush_task is timer_task:
                self._flush_task = None

    async def _flush_buffer(self) -> None:
        """Flush buffered records with batched relation resolution and write.

        The buffer is only cleared after a successful API write.  On failure
        records remain in the buffer so they can be retried on the next flush.
        """
        if not self._buffer:
            return
        items = self._buffer[:]
        current_task = asyncio.current_task()
        if self._flush_task is not None and self._flush_task is not current_task:
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
            cached = self._relation_cached_id(rel, value)
            if cached is not None:
                cache[(rel.target_field, value)] = cached
                return
            async with sem:
                matches = await self._find_relation_matches(rel, value)
                if len(matches) > 1:
                    raise FeishuBitablePayloadError(
                        f"relation field {rel.target_field!r} value {value!r} "
                        f"matched {len(matches)} records in table {rel.table_id!r}"
                    )
                if matches:
                    record_id = _record_id(matches[0])
                    cache[(rel.target_field, value)] = record_id
                    self._relation_cache_store(rel, value, record_id)
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
                        record_id = rec["record_id"]
                        cache[(rel.target_field, value)] = record_id
                        self._relation_cache_store(rel, value, record_id)

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
        timer_task: asyncio.Task[None] | None = None
        async with lock:
            if self._flush_task is not None:
                timer_task = self._flush_task
                timer_task.cancel()
                self._flush_task = None
        if timer_task is not None:
            await asyncio.gather(timer_task, return_exceptions=True)

        # Indexed insert close: drain all pending keys
        if self.insert_key_index and self._index_loaded:
            while True:
                await self._flush_indexed_insert(reason="close")
                async with lock:
                    has_buffered = bool(self._pending_order)
                if not has_buffered:
                    break
            # Surface any error stored from timer flush
            if self._flush_error is not None:
                err = self._flush_error
                self._flush_error = None
                raise err
            # No remaining pending keys should exist
            assert not self._pending_by_key, "close left pending entries"
            self._log_insert_lookup(outcome="success")
            return

        # Legacy close path
        async with lock:
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
                cached = self._relation_cached_id(relation, value)
                if cached is not None:
                    record_ids.append(cached)
                    continue
                matches = await self._find_relation_matches(relation, value)
                if len(matches) > 1:
                    raise FeishuBitablePayloadError(
                        f"relation field {relation.target_field!r} value {value!r} "
                        f"matched {len(matches)} records in table {relation.table_id!r}"
                    )
                if matches:
                    record_id = _record_id(matches[0])
                    self._relation_cache_store(relation, value, record_id)
                    record_ids.append(record_id)
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
                cached = self._relation_cached_id(relation, value)
                if cached is not None:
                    entry.record_id = cached
                    return cached
                matches = await self._find_relation_matches(relation, value)
                if len(matches) > 1:
                    raise FeishuBitablePayloadError(
                        f"relation field {relation.target_field!r} value {value!r} "
                        f"matched {len(matches)} records in table {relation.table_id!r}"
                    )
                if matches:
                    record_id = _record_id(matches[0])
                    self._relation_cache_store(relation, value, record_id)
                    return record_id
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
                self._relation_cache_store(relation, value, entry.record_id)
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
                        "cache": relation.cache,
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
