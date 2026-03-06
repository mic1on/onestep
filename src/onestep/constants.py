"""
常量定义模块

集中管理项目中的魔法数字、字符串等常量
"""
import os

# 默认配置
DEFAULT_GROUP = "OneStep"
DEFAULT_WORKERS = 1
# 从环境变量读取最大 worker 数量，默认 20
MAX_WORKERS = int(os.getenv("ONESTEP_MAX_WORKERS", "20"))

# 日志相关
LOG_FORMAT = "[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s"

# WebHook 相关
DEFAULT_WEBHOOK_HOST = "127.0.0.1"  # 默认只监听本地，更安全
DEFAULT_WEBHOOK_PORT = 8090

# Broker 消息重试
DEFAULT_SEND_RETRY_TIMES = 3
DEFAULT_SEND_RETRY_DELAY = 1.0

# 版本号（会从 __init__.py 读取，这里仅作为文档引用）
__all__ = [
    "DEFAULT_GROUP",
    "DEFAULT_WORKERS",
    "MAX_WORKERS",
    "LOG_FORMAT",
    "DEFAULT_WEBHOOK_HOST",
    "DEFAULT_WEBHOOK_PORT",
    "DEFAULT_SEND_RETRY_TIMES",
    "DEFAULT_SEND_RETRY_DELAY",
    "DEFAULT_MEMORY_BROKER_MAXSIZE",
    "DEFAULT_MEMORY_BROKER_TIMEOUT",
    "MILLISECONDS_PER_SECOND",
]


# Broker 相关配置
DEFAULT_MEMORY_BROKER_MAXSIZE = 1000  # MemoryBroker 默认队列大小，防止内存溢出
DEFAULT_MEMORY_BROKER_TIMEOUT = 5000  # Consumer 等待消息的超时时间（毫秒）

# 时间转换常量
MILLISECONDS_PER_SECOND = 1000  # 毫秒转秒的除数

