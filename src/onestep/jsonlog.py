"""JSON log formatting for the onestep CLI.

`StructuredEventLogger` (``onestep.events``) already attaches task lifecycle
fields (``event_kind``, ``task_name``, ``attempts``, ``failure_*`` ...) to log
records via ``extra``. Log collectors such as Loki or ELK, however, cannot
index fields buried in a human-readable line. ``--log-format json`` swaps the
CLI's stdout formatter for :class:`JsonLogFormatter` so every record the
runtime emits — lifecycle events *and* ordinary application/framework logs —
is one JSON object per line, using only the standard library.

Design notes:

- The formatter owns the whole line (``JSON + "\\n"``), so the handler must
  not prepend the default newline; ``JsonLogFormatter.attach`` installs a
  matching handler in one step.
- Well-known record attributes are mapped to stable keys (``ts``, ``level``,
  ``logger``, ``message``). Remaining ``record.__dict__`` entries that are not
  private/dunder and not logging bookkeeping are merged as ``extra`` fields,
  which is exactly where the ``StructuredEventLogger`` lifecycle fields land.
- Message arguments are left unformatted when they are lazy
  (``logger.info("... %s", obj)``) and serialization of any value fails: the
  original ``repr`` is kept instead of raising inside the logging machinery.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

__all__ = ["JsonLogFormatter"]

# Record attributes owned by the logging module itself; anything else that
# survives the filters below came in through ``extra={...}``.
_LOGGING_RESERVED = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)

# Fields the structured task-event logger attaches; they are promoted to the
# top level of the JSON object so log platforms can index them without
# touching the nested ``extra`` object.
_TASK_EVENT_FIELDS = (
    "event_kind",
    "app_name",
    "task_name",
    "source_name",
    "attempts",
    "duration_s",
    "emitted_at",
    "failure_kind",
    "failure_exception_type",
    "failure_message",
    "task_event_meta",
)


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseException):
        return f"{type(value).__name__}: {value}"
    return repr(value)


def _record_timestamp(record: logging.LogRecord) -> str:
    emitted_at = getattr(record, "emitted_at", None)
    if isinstance(emitted_at, str) and emitted_at:
        # TaskEvent timestamps (ISO 8601, timezone-aware) take precedence so
        # lifecycle lines carry the event's own clock, not the handler's.
        return emitted_at
    return datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()


class JsonLogFormatter(logging.Formatter):
    """Format each record as a single line of JSON.

    Lifecycle fields attached by ``StructuredEventLogger`` are promoted to
    the top level; any other non-standard record attributes are preserved
    under ``extra`` so nothing is silently dropped.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _record_timestamp(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for field in _TASK_EVENT_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)

        extras: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in _LOGGING_RESERVED or key in _TASK_EVENT_FIELDS:
                continue
            extras[key] = value
        if extras:
            payload["extra"] = extras

        if record.exc_info:
            # ``Formatter.format`` populates exc_text on the record; reuse it
            # when present so the traceback text is rendered once.
            if record.exc_text is None:
                record.exc_text = self.formatException(record.exc_info)
            payload["exc_info"] = record.exc_text
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)

        return json.dumps(payload, default=_json_default, ensure_ascii=False)

    def formatMessage(self, record: logging.LogRecord) -> str:
        # The full line is produced by ``format``; the base implementation's
        # %-style templating does not apply to JSON output.
        return record.getMessage()

    @staticmethod
    def attach(handler: logging.Handler) -> logging.Handler:
        """Install this formatter on ``handler`` for one-line JSON records."""
        handler.setFormatter(JsonLogFormatter())
        return handler
