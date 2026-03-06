"""
配置管理模块

支持从 YAML/JSON 文件加载配置，支持环境变量覆盖
"""
import logging
import os
import json
from pathlib import Path
from typing import Any, Dict, Optional, Union

logger = logging.getLogger(__name__)


class Config:
    """配置管理器"""

    def __init__(self,
                 config_file: Optional[Union[str, Path]] = None,
                 env_prefix: str = "ONESTEP"):
        """
        初始化配置管理器

        :param config_file: 配置文件路径（支持 .json, .yaml, .yml）
        :param env_prefix: 环境变量前缀（默认: "ONESTEP"）
        """
        self.config_file = Path(config_file) if config_file else None
        self.env_prefix = env_prefix.upper()
        self._config: Dict[str, Any] = {}

        # 如果指定了配置文件，则加载
        if self.config_file and self.config_file.exists():
            self.load_from_file(self.config_file)

    def load_from_file(self, config_file: Union[str, Path]) -> None:
        """
        从文件加载配置

        :param config_file: 配置文件路径
        :raises: FileNotFoundError 如果文件不存在
        :raises: ValueError 如果文件格式不支持
        """
        config_path = Path(config_file)

        if not config_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {config_path}")

        # 根据文件扩展名选择解析方式
        suffix = config_path.suffix.lower()

        if suffix == '.json':
            with open(config_path, 'r', encoding='utf-8') as f:
                self._config = json.load(f)
            logger.info(f"已加载 JSON 配置文件: {config_path}")

        elif suffix in ('.yaml', '.yml'):
            try:
                import yaml
                with open(config_path, 'r', encoding='utf-8') as f:
                    self._config = yaml.safe_load(f)
                logger.info(f"已加载 YAML 配置文件: {config_path}")
            except ImportError:
                raise ImportError(
                    "YAML 支持需要安装 PyYAML: pip install PyYAML"
                )
        else:
            raise ValueError(
                f"不支持的配置文件格式: {suffix}，"
                f"仅支持: .json, .yaml, .yml"
            )

    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置值

        优先级: 环境变量 > 配置文件 > 默认值

        :param key: 配置键（支持点分隔的嵌套键，如 'webhook.port'）
        :param default: 默认值
        :return: 配置值
        """
        # 1. 尝试从环境变量获取
        env_key = f"{self.env_prefix}_{key.upper().replace('.', '_')}"
        env_value = os.getenv(env_key)
        if env_value is not None:
            logger.debug(f"使用环境变量: {env_key}={env_value}")
            return self._parse_env_value(env_value)

        # 2. 尝试从配置文件获取
        value = self._get_nested_config(key)
        if value is not None:
            return value

        # 3. 返回默认值
        return default

    def set(self, key: str, value: Any) -> None:
        """
        设置配置值（仅在运行时有效）

        :param key: 配置键
        :param value: 配置值
        """
        self._set_nested_config(key, value)

    def get_all(self) -> Dict[str, Any]:
        """
        获取所有配置（不包括环境变量覆盖）

        :return: 配置字典
        """
        return self._config.copy()

    def _get_nested_config(self, key: str) -> Any:
        """
        获取嵌套配置值

        :param key: 配置键（支持点分隔的嵌套键）
        :return: 配置值，如果不存在则返回 None
        """
        keys = key.split('.')
        value = self._config

        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return None

        return value

    def _set_nested_config(self, key: str, value: Any) -> None:
        """
        设置嵌套配置值

        :param key: 配置键（支持点分隔的嵌套键）
        :param value: 配置值
        """
        keys = key.split('.')
        config = self._config

        # 导航到最后一级的父级
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]

        # 设置值
        config[keys[-1]] = value

    @staticmethod
    def _parse_env_value(value: str) -> Any:
        """
        解析环境变量值

        自动转换类型：数字、布尔值、null

        :param value: 环境变量值
        :return: 解析后的值
        """
        # 尝试转换为数字
        try:
            if '.' in value:
                return float(value)
            return int(value)
        except ValueError:
            pass

        # 尝试转换为布尔值
        if value.lower() in ('true', 'yes', 'on', '1'):
            return True
        if value.lower() in ('false', 'no', 'off', '0'):
            return False

        # 尝试转换为 null
        if value.lower() in ('null', 'none', ''):
            return None

        # 返回字符串
        return value

    def __repr__(self) -> str:
        return f"<Config file={self.config_file} prefix={self.env_prefix}>"
