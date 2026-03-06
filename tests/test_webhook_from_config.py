"""WebHookBroker.from_config() 测试"""
import os

import pytest

from onestep import Config, WebHookBroker


def test_webhook_from_config_with_all_params():
    """测试 from_config() 包含所有参数"""
    config = Config()
    config.set("webhook.host", "0.0.0.0")
    config.set("webhook.port", 9000)
    config.set("webhook.api_key", "secret-key")

    broker = WebHookBroker.from_config(config)

    assert broker.host == "0.0.0.0"
    assert broker.port == 9000
    assert broker.api_key == "secret-key"


def test_webhook_from_config_with_defaults():
    """测试 from_config() 使用默认值"""
    config = Config()

    # 不设置任何配置
    broker = WebHookBroker.from_config(config)

    assert broker.host == "127.0.0.1"  # 默认值
    assert broker.port == 8090  # 默认值
    assert broker.api_key is None  # 默认值


def test_webhook_from_config_with_partial_params():
    """测试 from_config() 部分配置"""
    config = Config()
    config.set("webhook.port", 8888)

    broker = WebHookBroker.from_config(config)

    assert broker.host == "127.0.0.1"  # 默认值
    assert broker.port == 8888  # 覆盖值
    assert broker.api_key is None  # 默认值


def test_webhook_from_config_custom_path():
    """测试 from_config() 自定义路径"""
    config = Config()
    config.set("webhook.port", 9000)

    broker = WebHookBroker.from_config(config, path="/custom-webhook")

    assert broker.path == "/custom-webhook"


def test_webhook_from_config_with_env_override():
    """测试 from_config() 环境变量覆盖"""
    os.environ["ONESTEP_WEBHOOK_PORT"] = "7777"

    try:
        config = Config()
        broker = WebHookBroker.from_config(config)

        assert broker.port == 7777  # 环境变量值
    finally:
        os.environ.pop("ONESTEP_WEBHOOK_PORT", None)


def test_webhook_comparison_old_vs_new():
    """对比旧写法和新写法的简洁性"""
    config = Config()
    config.set("webhook.port", 9000)
    config.set("webhook.api_key", "secret-key")

    # ❌ 旧写法（繁琐）
    broker_old = WebHookBroker(
        path="/webhook",
        host=config.get("webhook.host", "127.0.0.1"),
        port=config.get("webhook.port", 8090),
        api_key=config.get("webhook.api_key"),
    )

    # ✅ 新写法（简洁）
    broker_new = WebHookBroker.from_config(config)

    # 两者应该等价
    assert broker_old.host == broker_new.host
    assert broker_old.port == broker_new.port
    assert broker_old.api_key == broker_new.api_key
