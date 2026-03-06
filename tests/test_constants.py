"""常量模块测试"""
from onestep.constants import (
    DEFAULT_GROUP,
    DEFAULT_WORKERS,
    MAX_WORKERS,
    LOG_FORMAT,
    DEFAULT_WEBHOOK_HOST,
    DEFAULT_WEBHOOK_PORT,
    DEFAULT_SEND_RETRY_TIMES,
    DEFAULT_SEND_RETRY_DELAY,
)


def test_default_group():
    """测试默认分组"""
    assert DEFAULT_GROUP == "OneStep"


def test_default_workers():
    """测试默认 worker 数量"""
    assert DEFAULT_WORKERS == 1


def test_max_workers():
    """测试最大 worker 数量"""
    assert isinstance(MAX_WORKERS, int)
    assert MAX_WORKERS > 0


def test_log_format():
    """测试日志格式"""
    assert isinstance(LOG_FORMAT, str)
    assert "%(asctime)s" in LOG_FORMAT
    assert "%(name)s" in LOG_FORMAT
    assert "%(levelname)s" in LOG_FORMAT


def test_default_webhook_host():
    """测试默认 WebHook 主机"""
    assert DEFAULT_WEBHOOK_HOST == "127.0.0.1"  # 更安全的默认值，只监听本地


def test_default_webhook_port():
    """测试默认 WebHook 端口"""
    assert DEFAULT_WEBHOOK_PORT == 8090


def test_default_send_retry_times():
    """测试默认发送重试次数"""
    assert DEFAULT_SEND_RETRY_TIMES == 3


def test_default_send_retry_delay():
    """测试默认发送重试延迟"""
    assert DEFAULT_SEND_RETRY_DELAY == 1.0
