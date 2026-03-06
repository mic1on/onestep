"""配置管理测试"""
import json
import os
import tempfile
from pathlib import Path

import pytest

from onestep import Config


def test_config_from_json(tmp_path):
    """测试从 JSON 文件加载配置"""
    config_data = {
        "webhook": {
            "host": "0.0.0.0",
            "port": 9000
        },
        "workers": 10
    }

    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config_data), encoding='utf-8')

    config = Config(config_file=config_file)

    assert config.get("webhook.host") == "0.0.0.0"
    assert config.get("webhook.port") == 9000
    assert config.get("workers") == 10


def test_config_get_default():
    """测试获取不存在的键时返回默认值"""
    config = Config()

    assert config.get("nonexistent.key") is None
    assert config.get("nonexistent.key", "default") == "default"


def test_config_env_override():
    """测试环境变量覆盖配置"""
    # 设置环境变量
    os.environ["ONESTEP_WEBHOOK_PORT"] = "8888"
    os.environ["ONESTEP_WORKERS"] = "20"

    try:
        config = Config()

        # 环境变量应该覆盖配置文件和默认值
        assert config.get("webhook.port") == 8888
        assert config.get("workers") == 20
    finally:
        # 清理环境变量
        os.environ.pop("ONESTEP_WEBHOOK_PORT", None)
        os.environ.pop("ONESTEP_WORKERS", None)


def test_config_set_value():
    """测试设置配置值"""
    config = Config()

    config.set("custom.key", "value")
    assert config.get("custom.key") == "value"


def test_config_get_all():
    """测试获取所有配置"""
    config_data = {
        "key1": "value1",
        "nested": {
            "key2": "value2"
        }
    }

    config_file = Path(tempfile.mktemp(suffix=".json"))
    config_file.write_text(json.dumps(config_data), encoding='utf-8')

    try:
        config = Config(config_file=config_file)
        all_config = config.get_all()

        assert all_config["key1"] == "value1"
        assert all_config["nested"]["key2"] == "value2"
    finally:
        config_file.unlink()


def test_config_parse_env_numbers():
    """测试解析环境变量数字类型"""
    config = Config()

    os.environ["ONESTEP_TEST_INT"] = "123"
    os.environ["ONESTEP_TEST_FLOAT"] = "45.67"

    try:
        assert config.get("test.int") == 123
        assert config.get("test.float") == 45.67
    finally:
        os.environ.pop("ONESTEP_TEST_INT", None)
        os.environ.pop("ONESTEP_TEST_FLOAT", None)


def test_config_parse_env_boolean():
    """测试解析环境变量布尔类型"""
    config = Config()

    os.environ["ONESTEP_TEST_TRUE"] = "true"
    os.environ["ONESTEP_TEST_FALSE"] = "false"
    os.environ["ONESTEP_TEST_YES"] = "yes"
    os.environ["ONESTEP_TEST_NO"] = "no"

    try:
        assert config.get("test.true") is True
        assert config.get("test.false") is False
        assert config.get("test.yes") is True
        assert config.get("test.no") is False
    finally:
        os.environ.pop("ONESTEP_TEST_TRUE", None)
        os.environ.pop("ONESTEP_TEST_FALSE", None)
        os.environ.pop("ONESTEP_TEST_YES", None)
        os.environ.pop("ONESTEP_TEST_NO", None)


def test_config_parse_env_null():
    """测试解析环境变量 null 类型"""
    config = Config()

    os.environ["ONESTEP_TEST_NULL"] = "null"
    os.environ["ONESTEP_TEST_EMPTY"] = ""

    try:
        assert config.get("test.null") is None
        assert config.get("test.empty") is None
    finally:
        os.environ.pop("ONESTEP_TEST_NULL", None)
        os.environ.pop("ONESTEP_TEST_EMPTY", None)


def test_config_yaml_not_installed(tmp_path):
    """测试 YAML 文件在未安装 PyYAML 时的错误"""
    # 创建 YAML 文件
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("key: value\n", encoding='utf-8')

    # 临时隐藏 yaml 模块
    import sys
    yaml_module = sys.modules.get('yaml')
    if yaml_module:
        sys.modules['yaml'] = None

    try:
        with pytest.raises(ImportError, match="PyYAML"):
            Config(config_file=yaml_file)
    finally:
        # 恢复 yaml 模块
        if yaml_module:
            sys.modules['yaml'] = yaml_module


def test_config_unsupported_format(tmp_path):
    """测试不支持的配置文件格式"""
    txt_file = tmp_path / "config.txt"
    txt_file.write_text("key: value\n", encoding='utf-8')

    with pytest.raises(ValueError, match="不支持的配置文件格式"):
        Config(config_file=txt_file)


def test_config_nonexistent_file():
    """测试不存在的配置文件（不抛出异常，只是不加载）"""
    # 配置文件不存在时不应该抛出异常
    config = Config(config_file="/nonexistent/config.json")
    # 配置应该是空的
    assert config.get_all() == {}
    # 获取任何值都应该返回默认值
    assert config.get("any.key") is None
    assert config.get("any.key", "default") == "default"


def test_config_nested_key():
    """测试获取嵌套配置键"""
    config_data = {
        "level1": {
            "level2": {
                "level3": "deep_value"
            }
        }
    }

    config_file = Path(tempfile.mktemp(suffix=".json"))
    config_file.write_text(json.dumps(config_data), encoding='utf-8')

    try:
        config = Config(config_file=config_file)
        assert config.get("level1.level2.level3") == "deep_value"
    finally:
        config_file.unlink()


def test_config_set_nested_value():
    """测试设置嵌套配置值"""
    config = Config()

    config.set("level1.level2.level3", "value")
    assert config.get("level1.level2.level3") == "value"
    assert config.get_all()["level1"]["level2"]["level3"] == "value"


def test_config_env_prefix():
    """测试自定义环境变量前缀"""
    config = Config(env_prefix="MYAPP")

    os.environ["MYAPP_CUSTOM_KEY"] = "custom_value"

    try:
        assert config.get("custom.key") == "custom_value"
    finally:
        os.environ.pop("MYAPP_CUSTOM_KEY", None)
