from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from onestep.resilience import ConnectorErrorKind, ConnectorOperationError

_LOGGER_NAME = "onestep_feishu_bitable.connector"

_DEFAULT_BASE_URL = "https://open.feishu.cn"
_DEFAULT_TIMEOUT_S = 10.0
_DEFAULT_BATCH_SIZE = 100
DEFAULT_FALLBACK_SCAN_PAGE_LIMIT = 100
_MAX_PAGE_SIZE = 500
_TOKEN_REFRESH_MARGIN_S = 60.0
_REDACTED = "<redacted>"
_DEFAULT_INSERT_INDEX_PAGE_SIZE = 500
_DEFAULT_INSERT_INDEX_MAX_PAGES = 200
_DEFAULT_AMBIGUOUS_WRITE_MAX_ROUNDS = 3
_AUTOMATIC_CURSOR_FIELD_ALIASES = {
    "创建时间": "created_time",
    "最后修改时间": "last_modified_time",
    "最后更新时间": "last_modified_time",
}
_USER_ID_TYPES = frozenset({"open_id", "union_id", "user_id"})
_RELATION_FIELDS = frozenset(
    {"from", "app_token", "table_id", "key", "on_missing", "create_fields", "cache"}
)
_RELATION_MISSING_POLICIES = frozenset({"error", "empty", "create"})
_RELATION_CACHE_POLICIES = frozenset({"none", "lazy", "eager"})


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


@dataclass(frozen=True)
class _FeishuRelationConfig:
    target_field: str
    source_field: str
    app_token: str
    table_id: str
    key: str
    on_missing: str
    create_fields: Mapping[str, Any]
    cache: str = "none"


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


def _canonical_insert_key(value: Any) -> str:
    """Normalize a match field value to its canonical string form.

    Returns the canonical key or raises FeishuBitablePayloadError if the
    value cannot be used as a stable insert key.
    """
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise FeishuBitablePayloadError("insert key value is empty after stripping")
        return stripped
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        if value != value or abs(value) == float("inf"):
            raise FeishuBitablePayloadError(
                "insert key value must be a finite number"
            )
        return str(value)
    if value is None:
        raise FeishuBitablePayloadError("insert key value must not be None")
    if isinstance(value, (dict, list, set, tuple)):
        raise FeishuBitablePayloadError(
            "insert key value must be a string, number, or boolean, "
            "got {!r}".format(type(value).__name__)
        )
    raise FeishuBitablePayloadError(
        "insert key value must be a string, number, or boolean, "
        "got {!r}".format(type(value).__name__)
    )


def _normalize_insert_key_index(value: bool) -> bool:
    if not isinstance(value, bool):
        raise TypeError("'insert_key_index' must be a boolean")
    return value


def _normalize_insert_index_page_size(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("'insert_index_page_size' must be an integer")
    if value < 1:
        raise ValueError("'insert_index_page_size' must be >= 1")
    return min(value, _MAX_PAGE_SIZE)


def _normalize_insert_index_max_pages(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("'insert_index_max_pages' must be an integer")
    if value < 1:
        raise ValueError("'insert_index_max_pages' must be >= 1")
    return value


def _normalize_ambiguous_write_max_rounds(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("'ambiguous_write_max_rounds' must be an integer")
    if value < 1:
        raise ValueError("'ambiguous_write_max_rounds' must be >= 1")
    return value


def _validate_insert_key_index_requirements(
    *,
    mode: str,
    match_fields: tuple[str, ...],
) -> None:
    if mode != "insert":
        raise ValueError(
            "insert_key_index requires mode='insert', not {!r}".format(mode)
        )
    if len(match_fields) != 1:
        raise ValueError(
            "insert_key_index requires exactly one match field, "
            "got {}: {!r}".format(len(match_fields), match_fields)
        )


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


def _is_definite_write_error(exc: ConnectorOperationError) -> bool:
    """Check if a connector error is a definite (non-ambiguous) failure."""
    return exc.kind in {
        ConnectorErrorKind.PERMANENT,
        ConnectorErrorKind.MISCONFIGURED,
    }


def _batch_create_response_is_complete(
    result: Mapping[str, Any], *, expected: int
) -> bool:
    """Return whether Feishu assigned one response record per input."""
    records = result.get("records")
    if not isinstance(records, list) or len(records) != expected:
        return False
    return all(isinstance(record, Mapping) for record in records)


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

        cache = _require_non_empty_string(
            raw_config.get("cache", "none"),
            field=f"{field}.cache",
        ).lower()
        if cache not in _RELATION_CACHE_POLICIES:
            raise ValueError(f"'{field}.cache' must be one of 'none', 'lazy', or 'eager'")

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
                cache=cache,
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
    _logger = logging.getLogger(_LOGGER_NAME)
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
