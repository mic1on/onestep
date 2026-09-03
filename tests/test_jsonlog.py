"""Unit coverage for the JSON log formatter (``--log-format json``)."""

from __future__ import annotations

import json
import logging

from onestep.events import FailureInfo, FailureKind, StructuredEventLogger, TaskEvent, TaskEventKind
from onestep.jsonlog import JsonLogFormatter


class ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


def _make_logger(name: str, handler: logging.Handler) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger


def test_plain_record_is_valid_single_line_json() -> None:
    handler = ListHandler()
    handler.setFormatter(JsonLogFormatter())
    logger = _make_logger("tests.jsonlog.plain", handler)

    logger.info("hello %s", "world")

    assert len(handler.lines) == 1
    payload = json.loads(handler.lines[0])
    assert payload["level"] == "INFO"
    assert payload["logger"] == "tests.jsonlog.plain"
    assert payload["message"] == "hello world"
    assert payload["ts"]
    assert "\n" not in handler.lines[0].rstrip("\n")


def test_task_event_fields_promoted_to_top_level() -> None:
    handler = ListHandler()
    handler.setFormatter(JsonLogFormatter())
    logger = _make_logger("tests.jsonlog.events", handler)

    event_logger = StructuredEventLogger(logger=logger)
    event_logger(
        TaskEvent(
            kind=TaskEventKind.SUCCEEDED,
            app="billing",
            task="sync",
            source="queue.in",
            attempts=2,
            duration_s=0.25,
        )
    )

    payload = json.loads(handler.lines[0])
    assert payload["event_kind"] == "succeeded"
    assert payload["app_name"] == "billing"
    assert payload["task_name"] == "sync"
    assert payload["source_name"] == "queue.in"
    assert payload["attempts"] == 2
    assert payload["duration_s"] == 0.25
    # TaskEvent clock wins over the handler's wall clock.
    assert payload["ts"] == payload["emitted_at"]
    assert payload["message"] == "task succeeded"
    assert payload["level"] == "INFO"


def test_failure_event_fields_serialized() -> None:
    handler = ListHandler()
    handler.setFormatter(JsonLogFormatter())
    logger = _make_logger("tests.jsonlog.failure", handler)

    event_logger = StructuredEventLogger(logger=logger)
    event_logger(
        TaskEvent(
            kind=TaskEventKind.FAILED,
            app="billing",
            task="sync",
            source="queue.in",
            attempts=3,
            failure=FailureInfo(
                kind=FailureKind.ERROR,
                exception_type="TimeoutError",
                message="upstream timed out",
            ),
        )
    )

    payload = json.loads(handler.lines[0])
    assert payload["level"] == "ERROR"
    assert payload["event_kind"] == "failed"
    assert payload["failure_kind"] == "error"
    assert payload["failure_exception_type"] == "TimeoutError"
    assert payload["failure_message"] == "upstream timed out"
    assert payload["failure_kind"] not in payload.get("extra", {})


def test_event_meta_nested_in_output() -> None:
    handler = ListHandler()
    handler.setFormatter(JsonLogFormatter())
    logger = _make_logger("tests.jsonlog.meta", handler)

    event_logger = StructuredEventLogger(logger=logger)
    event_logger(
        TaskEvent(
            kind=TaskEventKind.FETCHED,
            app="billing",
            task="sync",
            source="queue.in",
            attempts=0,
            meta={"lease_id": "abc"},
        )
    )

    payload = json.loads(handler.lines[0])
    assert payload["task_event_meta"] == {"lease_id": "abc"}


def test_unknown_extra_attributes_land_in_extra_object() -> None:
    handler = ListHandler()
    handler.setFormatter(JsonLogFormatter())
    logger = _make_logger("tests.jsonlog.extra", handler)

    logger.warning("watch out", extra={"request_id": "req-1", "tenant": "acme"})

    payload = json.loads(handler.lines[0])
    assert payload["extra"] == {"request_id": "req-1", "tenant": "acme"}


def test_unserializable_extra_values_do_not_break_formatting() -> None:
    handler = ListHandler()
    handler.setFormatter(JsonLogFormatter())
    logger = _make_logger("tests.jsonlog.unserializable", handler)

    class Opaque:
        def __repr__(self) -> str:
            return "<opaque>"

    logger.info("carrying", extra={"opaque": Opaque()})

    payload = json.loads(handler.lines[0])
    assert payload["extra"]["opaque"] == "<opaque>"


def test_exc_info_and_stack_info_included() -> None:
    handler = ListHandler()
    handler.setFormatter(JsonLogFormatter())
    logger = _make_logger("tests.jsonlog.exc", handler)

    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("caught")

    payload = json.loads(handler.lines[0])
    assert "ValueError: boom" in payload["exc_info"]
    assert "Traceback (most recent call last)" in payload["exc_info"]


def test_lazy_args_not_leaking_tuple_into_json() -> None:
    handler = ListHandler()
    handler.setFormatter(JsonLogFormatter())
    logger = _make_logger("tests.jsonlog.lazyargs", handler)

    logger.info("count=%d", 3)

    payload = json.loads(handler.lines[0])
    assert payload["message"] == "count=3"
    assert "args" not in payload


def test_attach_installs_formatter_on_handler() -> None:
    handler = logging.StreamHandler()
    returned = JsonLogFormatter.attach(handler)
    assert returned is handler
    assert isinstance(handler.formatter, JsonLogFormatter)
