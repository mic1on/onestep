"""Regression tests for credential redaction in Redis connector errors."""
from __future__ import annotations

import traceback
from unittest.mock import AsyncMock, patch

import pytest

from onestep import (
    ConnectorErrorKind,
    ConnectorOperationError,
    Envelope,
)
from onestep_redis import RedisConnector
from onestep_redis.resilience import (
    collect_sensitive_tokens,
    redacted_redis_cause,
)


class _RedisURLError(ConnectionError):
    """Simulates a redis-py connection error that embeds the full URL."""


def test_collect_sensitive_tokens_extracts_url_userinfo_and_options() -> None:
    tokens = collect_sensitive_tokens(
        "redis://:s3cret@redis.internal:6379/0",
        {"password": "opts-pw", "db": 0},
    )
    assert "redis://:s3cret@redis.internal:6379/0" in tokens
    assert "s3cret" in tokens
    assert "opts-pw" in tokens
    assert 0 not in tokens and "0" not in [t for t in tokens if t == "0"]


def test_redacted_redis_cause_scrubs_credentials() -> None:
    secret_url = "redis://reader:s3cret@redis.internal:6379/0"
    exc = _RedisURLError(f"Error connecting to {secret_url}: AUTH failed")
    cause = redacted_redis_cause(exc, secrets=[secret_url, "s3cret"])
    rendered = str(cause)
    assert "s3cret" not in rendered
    assert "reader:s3cret" not in rendered
    assert "<redacted>" in rendered


@pytest.mark.asyncio
async def test_send_failure_does_not_leak_url_credentials() -> None:
    secret_url = "redis://reader:s3cret@redis.internal:6379/0"
    connector = RedisConnector(secret_url)
    stream = connector.stream("events")

    mock_redis = AsyncMock()
    mock_redis.xgroup_create = AsyncMock()
    mock_redis.xadd = AsyncMock(
        side_effect=_RedisURLError(f"Error connecting to {secret_url}: AUTH failed")
    )
    mock_redis.aclose = AsyncMock()

    with patch.object(connector, "acquire", return_value=mock_redis):
        with pytest.raises(ConnectorOperationError) as captured:
            await stream.send(Envelope(body={"id": 1}))

    error = captured.value
    # The public ``cause`` must not carry credentials.
    assert "s3cret" not in str(error.cause)
    assert "reader:s3cret" not in str(error.cause)
    assert "<redacted>" in str(error.cause)
    # The original secret-bearing exception must not be chained.
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    # The formatted traceback (reported by the runtime) stays clean too.
    traceback_text = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    assert "s3cret" not in traceback_text
    assert "reader:s3cret" not in traceback_text
