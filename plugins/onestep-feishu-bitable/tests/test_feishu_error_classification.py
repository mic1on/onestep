"""Error-classification tests for ``_classify_api_error``.

Feishu returns most *business* failures inside an **HTTP 200** body, so the
numeric business ``code`` field -- not the HTTP status and not the ``msg``
wording -- is the reliable signal.  These tests pin that contract.

Official error-code references (the doc site is a JS SPA, so the portal
content endpoint is used; ``fullPath`` must not repeat ``/document``):

* https://open.feishu.cn/document_portal/v1/document/get_detail?fullPath=/docs/bitable-v1/app-table-record/search
* human URL: https://open.feishu.cn/document/docs/bitable-v1/app-table-record/search
"""

from __future__ import annotations

import pytest

from onestep.resilience import ConnectorErrorKind, is_retryable_connector_error
from onestep_feishu_bitable._shared import _classify_api_error

RETRYABLE = frozenset(
    {
        ConnectorErrorKind.DISCONNECTED,
        ConnectorErrorKind.TRANSIENT,
        ConnectorErrorKind.THROTTLED,
    }
)

# (status, code, official msg, expected kind) -- all four codes the official
# table tells developers to retry, delivered in the HTTP 200 body Feishu
# actually uses for business errors.
RETRYABLE_CODE_CASES = [
    pytest.param(200, 1254290, "TooManyRequest", ConnectorErrorKind.THROTTLED, id="1254290-too-many-request"),
    pytest.param(200, 1254291, "Write conflict", ConnectorErrorKind.THROTTLED, id="1254291-write-conflict"),
    pytest.param(
        400,
        1254607,
        "Data not ready, please try again later",
        ConnectorErrorKind.TRANSIENT,
        id="1254607-data-not-ready",
    ),
    pytest.param(200, 1254002, "Fail", ConnectorErrorKind.TRANSIENT, id="1254002-fail"),
]


@pytest.mark.parametrize(("status", "code", "message", "expected"), RETRYABLE_CODE_CASES)
def test_officially_retryable_codes_classify_as_retryable(
    status: int, code: int, message: str, expected: ConnectorErrorKind
) -> None:
    kind = _classify_api_error(status=status, code=code, message=message)

    assert kind is expected
    assert kind in RETRYABLE
    assert is_retryable_connector_error(kind) is True


@pytest.mark.parametrize(("status", "code", "message", "expected"), RETRYABLE_CODE_CASES)
def test_retryable_codes_ignore_http_200_business_error_wrapper(
    status: int, code: int, message: str, expected: ConnectorErrorKind
) -> None:
    """A code alone must decide, even with an empty or unrelated msg."""
    assert _classify_api_error(status=200, code=code, message="") is expected


@pytest.mark.parametrize(
    "code",
    [1254100, 1254101, 1254102, 1254103, 1254104, 1254107, 1254108, 1254109, 1254130, 1254030],
)
@pytest.mark.parametrize("message", ["RecordAddOnceExceedLimit", "", "请求过快"])
def test_permanent_quota_codes_are_never_throttled(code: int, message: str) -> None:
    """*ExceedLimit quota codes must not become THROTTLED via the word "limit"."""
    kind = _classify_api_error(status=200, code=code, message=message)

    assert kind is ConnectorErrorKind.PERMANENT
    assert kind is not ConnectorErrorKind.THROTTLED
    assert is_retryable_connector_error(kind) is False


def test_record_add_once_exceed_limit_is_permanent_not_throttled() -> None:
    """Regression for the infinite retry loop on 1254104 (audit bug B2)."""
    kind = _classify_api_error(
        status=200,
        code=1254104,
        message="RecordAddOnceExceedLimit 单次添加记录数量超限",
    )

    assert kind is ConnectorErrorKind.PERMANENT
    assert is_retryable_connector_error(kind) is False


# The same rate-limit condition worded differently must classify identically.
RATE_LIMIT_MESSAGES = [
    pytest.param("Request was throttled", id="throttled"),
    pytest.param("request trigger frequency limit", id="legacy-frequency-limit"),
    pytest.param("Too many requests, please slow down", id="too-many-requests"),
    pytest.param("rate limit exceeded", id="rate-limit"),
    pytest.param("QPS limit exceeded", id="qps"),
    pytest.param("频率限制", id="zh-frequency-limit"),
    pytest.param("请求过快，稍后重试", id="zh-too-fast"),
    pytest.param("请求过于频繁", id="zh-too-frequent"),
    pytest.param("触发频率限制，请稍后重试", id="zh-trigger-frequency"),
]


@pytest.mark.parametrize("message", RATE_LIMIT_MESSAGES)
def test_rate_limit_wording_classifies_throttled_in_english_and_chinese(message: str) -> None:
    kind = _classify_api_error(status=200, code=None, message=message)

    assert kind is ConnectorErrorKind.THROTTLED
    assert is_retryable_connector_error(kind) is True


def test_rate_limit_wording_is_stable_across_message_variants() -> None:
    """Regression for wording-dependent classification (audit bug B3/H5)."""
    kinds = {
        _classify_api_error(status=200, code=None, message=message)
        for message in (
            "Request was throttled",
            "request trigger frequency limit",
            "频率限制",
            "请求过快，稍后重试",
        )
    }

    assert kinds == {ConnectorErrorKind.THROTTLED}


def test_known_code_beats_contradictory_message_wording() -> None:
    """The code table is authoritative over substring heuristics."""
    kind = _classify_api_error(
        status=200,
        code=1254290,
        message="RecordAddOnceExceedLimit",  # contains "limit"
    )

    assert kind is ConnectorErrorKind.THROTTLED


PERMANENT_CASES = [
    pytest.param(400, 1254000, "WrongRequestJson", id="wrong-request-json"),
    pytest.param(400, 1254001, "WrongRequestBody", id="wrong-request-body"),
    pytest.param(400, 1254024, "InvalidFieldNames", id="invalid-field-names"),
    pytest.param(400, None, "invalid field", id="invalid-field-no-code"),
    pytest.param(200, None, "field validation failed", id="field-validation-failed"),
]


@pytest.mark.parametrize(("status", "code", "message"), PERMANENT_CASES)
def test_genuinely_permanent_errors_stay_permanent(status: int, code: int | None, message: str) -> None:
    kind = _classify_api_error(status=status, code=code, message=message)

    assert kind is ConnectorErrorKind.PERMANENT
    assert is_retryable_connector_error(kind) is False


MISCONFIGURED_CASES = [
    pytest.param(200, 99991663, "Invalid access token for authorization", id="99991663-invalid-token"),
    pytest.param(200, 99991664, "invalid app token", id="99991664-invalid-app-token"),
    pytest.param(200, 99991665, "invalid tenant code", id="99991665-invalid-tenant-code"),
    pytest.param(200, 1254036, "Bitable is copying, please try again later.", id="1254036-copying"),
]


@pytest.mark.parametrize(("status", "code", "message"), MISCONFIGURED_CASES)
def test_credentials_and_unusable_base_are_misconfigured(
    status: int, code: int, message: str
) -> None:
    """These were previously decided by msg wording and drifted with it."""
    kind = _classify_api_error(status=status, code=code, message=message)

    assert kind is ConnectorErrorKind.MISCONFIGURED
    assert is_retryable_connector_error(kind) is False


@pytest.mark.parametrize("message", ["", "错误", "unexpected payload"])
def test_auth_codes_do_not_become_throttled_when_msg_is_missing(message: str) -> None:
    """Regression for the unreachable 999916xx branch (audit bug B3)."""
    kind = _classify_api_error(status=200, code=99991663, message=message)

    assert kind is ConnectorErrorKind.MISCONFIGURED
    assert kind is not ConnectorErrorKind.THROTTLED


@pytest.mark.parametrize(
    ("status", "code", "message", "expected"),
    [
        (200, 1255001, "InternalError", ConnectorErrorKind.TRANSIENT),
        (200, 1255002, "RpcError", ConnectorErrorKind.TRANSIENT),
        (200, 1255003, "MarshalError", ConnectorErrorKind.TRANSIENT),
        (200, 1255004, "UmMarshalError", ConnectorErrorKind.TRANSIENT),
        (200, 1255005, "ConvError", ConnectorErrorKind.TRANSIENT),
        (504, 1255040, "Request timed out, please try again later", ConnectorErrorKind.TRANSIENT),
    ],
)
def test_internal_and_timeout_codes_are_transient(
    status: int, code: int, message: str, expected: ConnectorErrorKind
) -> None:
    kind = _classify_api_error(status=status, code=code, message=message)

    assert kind is expected
    assert is_retryable_connector_error(kind) is True


def test_legacy_frequency_control_code_is_throttled_without_msg() -> None:
    """Legacy OpenAPI reports rate limiting as HTTP 400 + code 99991400."""
    kind = _classify_api_error(status=400, code=99991400, message="")

    assert kind is ConnectorErrorKind.THROTTLED
    assert is_retryable_connector_error(kind) is True


@pytest.mark.parametrize(
    ("status", "code", "message", "expected"),
    [
        (429, None, "", ConnectorErrorKind.THROTTLED),
        (503, None, "Service Unavailable", ConnectorErrorKind.TRANSIENT),
        (500, None, "Internal Server Error", ConnectorErrorKind.TRANSIENT),
        (401, None, "", ConnectorErrorKind.MISCONFIGURED),
        (403, None, "", ConnectorErrorKind.MISCONFIGURED),
        (404, None, "", ConnectorErrorKind.MISCONFIGURED),
    ],
)
def test_absent_code_falls_back_to_http_status(
    status: int, code: int | None, message: str, expected: ConnectorErrorKind
) -> None:
    assert _classify_api_error(status=status, code=code, message=message) is expected


def test_unknown_code_on_http_200_does_not_become_retryable() -> None:
    """An unrecognized code with no retry wording stays a deterministic failure."""
    kind = _classify_api_error(status=200, code=1254999, message="something new")

    assert kind is ConnectorErrorKind.PERMANENT
    assert is_retryable_connector_error(kind) is False


def test_http_429_outranks_a_contradictory_business_code() -> None:
    """A transport-level 429 is unambiguous and must win over the code table.

    Real gateways pair 429 with an auth-shaped legacy code in the body; the
    response status is what actually describes the failure.
    """
    kind = _classify_api_error(status=429, code=99991663, message="rate limit")

    assert kind is ConnectorErrorKind.THROTTLED
    assert is_retryable_connector_error(kind) is True


@pytest.mark.parametrize("code", [99991663, 1254290, 1254104, None])
def test_http_5xx_outranks_the_code_table(code: int | None) -> None:
    """A 5xx means the server failed before it could judge the request."""
    kind = _classify_api_error(status=503, code=code, message="Service Unavailable")

    assert kind is ConnectorErrorKind.TRANSIENT
    assert is_retryable_connector_error(kind) is True


def test_connection_errors_are_not_reclassified_by_the_code_table() -> None:
    """DISCONNECTED (transport) stays retryable and is never produced here."""
    assert is_retryable_connector_error(ConnectorErrorKind.DISCONNECTED) is True
    assert _classify_api_error(
        status=503, code=None, message="connection reset"
    ) is ConnectorErrorKind.TRANSIENT
