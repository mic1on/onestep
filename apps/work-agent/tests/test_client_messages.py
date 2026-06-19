from __future__ import annotations

import json
from uuid import UUID

from onestep_worker_agent.client import (
    _handle_error_message,
    _parse_hello_ack,
    _payload_timeout_s,
    _worker_agent_ws_url,
    build_heartbeat_message,
    build_hello_message,
)


def test_build_hello_message() -> None:
    message = build_hello_message(
        worker_agent_id=UUID("11111111-1111-4111-8111-111111111111"),
        max_concurrent_deployments=2,
        used_slots=1,
        running_deployments=["deployment-1"],
    )

    assert message["type"] == "hello"
    assert message["payload"]["protocol_version"] == "1"
    assert message["payload"]["worker_agent_id"] == "11111111-1111-4111-8111-111111111111"
    assert message["payload"]["used_slots"] == 1


def test_build_heartbeat_message() -> None:
    message = build_heartbeat_message(
        worker_agent_id=UUID("11111111-1111-4111-8111-111111111111"),
        used_slots=0,
        running_deployments=[],
    )

    assert message["type"] == "heartbeat"
    assert message["payload"]["running_deployments"] == []


def test_worker_agent_ws_url_uses_plane_host() -> None:
    assert _worker_agent_ws_url("http://localhost:8000") == (
        "ws://localhost:8000/api/v1/worker-agents/ws"
    )
    assert _worker_agent_ws_url("https://cp.example.com/base") == (
        "wss://cp.example.com/api/v1/worker-agents/ws"
    )


def test_parse_hello_ack_reads_heartbeat_interval() -> None:
    raw = json.dumps(
        {
            "type": "hello_ack",
            "payload": {"heartbeat_interval_s": 15},
        }
    )
    assert _parse_hello_ack(raw, default=30) == 15


def test_parse_hello_ack_falls_back_to_default_when_missing() -> None:
    raw = json.dumps({"type": "hello_ack", "payload": {}})
    assert _parse_hello_ack(raw, default=30) == 30


def test_parse_hello_ack_falls_back_to_default_on_invalid_json() -> None:
    assert _parse_hello_ack("not-json", default=30) == 30


def test_parse_hello_ack_rejects_non_positive_interval() -> None:
    raw = json.dumps({"type": "hello_ack", "payload": {"heartbeat_interval_s": 0}})
    assert _parse_hello_ack(raw, default=30) == 30


def test_payload_timeout_s_reads_command_timeout() -> None:
    assert _payload_timeout_s({"timeout_s": 45}) == 45.0


def test_payload_timeout_s_returns_none_when_absent() -> None:
    assert _payload_timeout_s({}) is None


def test_payload_timeout_s_rejects_non_positive() -> None:
    assert _payload_timeout_s({"timeout_s": 0}) is None
    assert _payload_timeout_s({"timeout_s": -5}) is None


def test_payload_timeout_s_rejects_bool() -> None:
    # bool is a subclass of int; it must not be treated as a timeout.
    assert _payload_timeout_s({"timeout_s": True}) is None


def test_handle_error_message_logs_code_and_message(capsys) -> None:
    _handle_error_message(
        {
            "type": "error",
            "payload": {
                "code": "hello_required",
                "message": "first message must be hello",
                "close_connection": True,
            },
        }
    )
    captured = capsys.readouterr()
    assert "hello_required" in captured.out
    assert "first message must be hello" in captured.out
    assert "closed" in captured.out


def test_handle_error_message_handles_missing_payload(capsys) -> None:
    _handle_error_message({"type": "error"})
    captured = capsys.readouterr()
    assert "error frame" in captured.out

