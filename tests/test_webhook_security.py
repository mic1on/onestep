"""WebHookBroker 安全测试"""
import threading
import time
from queue import Queue

import pytest

from onestep import WebHookBroker
from onestep.broker.webhook import WebHookServer


def test_webhook_default_host_is_localhost():
    """测试 WebHook 默认主机是 localhost，不是 0.0.0.0"""
    broker = WebHookBroker(path="/test")
    assert broker.host == "127.0.0.1", "默认应该监听 localhost，更安全"


def test_webhook_custom_host():
    """测试 WebHook 可以自定义主机"""
    broker = WebHookBroker(path="/test", host="0.0.0.0")
    assert broker.host == "0.0.0.0"


def test_webhook_api_key_enabled():
    """测试 WebHook 可以启用 API key 认证"""
    broker = WebHookBroker(path="/test", api_key="secret-key")
    assert broker.api_key == "secret-key"
    assert WebHookServer.api_key == "secret-key"


def test_webhook_no_api_key_by_default():
    """测试 WebHook 默认不启用 API key"""
    broker = WebHookBroker(path="/test")
    assert broker.api_key is None


def test_webhook_shutdown_cleans_resources():
    """测试 WebHook shutdown 会清理资源"""
    from onestep.broker.webhook import WebHookBroker as WHBroker

    # 创建 broker
    broker = WHBroker(path="/test")
    address = (broker.host, broker.port)

    # 启动服务器
    consumer = broker.consume()

    # 等待服务器启动
    time.sleep(0.1)

    # 检查服务器已注册
    assert address in WHBroker._servers, "服务器应该在 _servers 中"
    assert address in WebHookServer.servers, "路径应该在 servers 中"

    # 关闭
    broker.shutdown()

    # 检查资源已清理
    assert address not in WHBroker._servers, "服务器应该从 _servers 中删除"
    assert address not in WebHookServer.servers, "路径应该从 servers 中删除"


def test_webhook_concurrent_shutdown():
    """测试并发调用 shutdown 不会出错"""
    broker = WebHookBroker(path="/test")
    consumer = broker.consume()

    time.sleep(0.1)

    # 并发关闭
    threads = []
    for _ in range(5):
        t = threading.Thread(target=broker.shutdown)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    # 验证资源已清理
    assert (broker.host, broker.port) not in broker._servers
