"""消息脱敏测试"""
import os

import pytest

from onestep import Message


def test_message_redact_password():
    """测试密码字段脱敏"""
    message = Message(body={"password": "secret123", "username": "admin"})
    result = message.to_dict(redact_sensitive=True)

    assert result["body"]["password"] == "***REDACTED***"
    assert result["body"]["username"] == "admin"


def test_message_redact_nested():
    """测试嵌套字典脱敏"""
    message = Message(body={
        "user": {
            "name": "John",
            "email": "john@example.com"
        }
    })
    result = message.to_dict(redact_sensitive=True)

    assert result["body"]["user"]["name"] == "John"
    assert result["body"]["user"]["email"] == "***REDACTED***"


def test_message_redact_list():
    """测试列表中的字典脱敏"""
    message = Message(body=[
        {"name": "John", "token": "abc123"},
        {"name": "Jane", "token": "xyz789"}
    ])
    result = message.to_dict(redact_sensitive=True)

    assert result["body"][0]["name"] == "John"
    assert result["body"][0]["token"] == "***REDACTED***"
    assert result["body"][1]["name"] == "Jane"
    assert result["body"][1]["token"] == "***REDACTED***"


def test_message_redact_custom_keys():
    """测试自定义敏感键"""
    message = Message(body={"custom_field": "secret", "normal_field": "public"})

    # 使用自定义敏感键列表
    result = message.to_dict(redact_sensitive=True, sensitive_keys=["custom_field"])

    assert result["body"]["custom_field"] == "***REDACTED***"
    assert result["body"]["normal_field"] == "public"


def test_message_redact_all_default_keys():
    """测试所有默认敏感键"""
    sensitive_data = {
        "password": "secret123",
        "secret": "top_secret",
        "token": "abc123",
        "api_key": "xyz789",
        "authorization": "Bearer token",
        "auth": "credentials",
        "credit_card": "4111111111111111",
        "ssn": "123-45-6789",
        "phone": "123-456-7890",
        "email": "test@example.com",
        "normal_field": "public"
    }

    message = Message(body=sensitive_data)
    result = message.to_dict(redact_sensitive=True)

    # 所有敏感字段都应该被脱敏
    assert result["body"]["password"] == "***REDACTED***"
    assert result["body"]["secret"] == "***REDACTED***"
    assert result["body"]["token"] == "***REDACTED***"
    assert result["body"]["api_key"] == "***REDACTED***"
    assert result["body"]["authorization"] == "***REDACTED***"
    assert result["body"]["auth"] == "***REDACTED***"
    assert result["body"]["credit_card"] == "***REDACTED***"
    assert result["body"]["ssn"] == "***REDACTED***"
    assert result["body"]["phone"] == "***REDACTED***"
    assert result["body"]["email"] == "***REDACTED***"

    # 正常字段不应该被脱敏
    assert result["body"]["normal_field"] == "public"


def test_message_redact_case_insensitive():
    """测试键名不区分大小写"""
    message = Message(body={"PASSWORD": "secret", "UserEmail": "test@example.com"})
    result = message.to_dict(redact_sensitive=True)

    assert result["body"]["PASSWORD"] == "***REDACTED***"
    assert result["body"]["UserEmail"] == "***REDACTED***"


def test_message_str_redaction_enabled():
    """测试 __str__ 方法默认启用脱敏"""
    os.environ["ONESTEP_LOG_REDACT"] = "true"

    try:
        message = Message(body={"password": "secret123", "username": "admin"})
        str_result = str(message)

        # 字符串应该包含脱敏后的数据
        assert "***REDACTED***" in str_result
        assert "secret123" not in str_result
    finally:
        os.environ.pop("ONESTEP_LOG_REDACT", None)


def test_message_str_redaction_disabled():
    """测试 __str__ 方法可以禁用脱敏"""
    os.environ["ONESTEP_LOG_REDACT"] = "false"

    try:
        message = Message(body={"password": "secret123", "username": "admin"})
        str_result = str(message)

        # 字符串应该包含原始数据
        assert "secret123" in str_result
        assert "***REDACTED***" not in str_result
    finally:
        os.environ.pop("ONESTEP_LOG_REDACT", None)


def test_message_no_redaction_by_default():
    """测试默认行为（环境变量未设置时启用脱敏）"""
    # 确保环境变量未设置
    os.environ.pop("ONESTEP_LOG_REDACT", None)

    message = Message(body={"password": "secret123"})
    str_result = str(message)

    # 默认应该启用脱敏
    assert "***REDACTED***" in str_result
    assert "secret123" not in str_result


def test_message_redact_with_string_body():
    """测试字符串 body 不需要脱敏"""
    message = Message(body="plain string message")
    result = message.to_dict(redact_sensitive=True)

    # 字符串应该保持不变
    assert result["body"] == "plain string message"


def test_message_redact_with_number_body():
    """测试数字 body 不需要脱敏"""
    message = Message(body=12345)
    result = message.to_dict(redact_sensitive=True)

    # 数字应该保持不变
    assert result["body"] == 12345


def test_message_partial_key_match():
    """测试部分键名匹配"""
    message = Message(body={
        "user_password": "secret",
        "confirm_password": "secret",
        "old_password": "secret"
    })
    result = message.to_dict(redact_sensitive=True)

    # 包含 password 的所有字段都应该被脱敏
    assert result["body"]["user_password"] == "***REDACTED***"
    assert result["body"]["confirm_password"] == "***REDACTED***"
    assert result["body"]["old_password"] == "***REDACTED***"
