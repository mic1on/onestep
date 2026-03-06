"""
使用配置文件的示例

展示如何使用 Config 类加载配置并应用到 OneStep
"""
from onestep import step, WebHookBroker, Config


# 示例 1: 从配置文件加载
config = Config(config_file=".onestep.yml")

# 使用配置创建 WebHook Broker
@step(
    from_broker=WebHookBroker(
        path="/webhook",
        host=config.get("webhook.host", "127.0.0.1"),
        port=config.get("webhook.port", 8090),
        api_key=config.get("webhook.api_key"),
    ),
    workers=config.get("workers.default", 1),
)
def handle_webhook(message):
    print(f"收到消息: {message}")


# 示例 2: 使用环境变量覆盖
# 环境变量格式: ONESTEP_<CONFIG_KEY>
# 例如: ONESTEP_WEBHOOK_PORT=9000
# 注意: 嵌套键使用下划线分隔

# 设置环境变量后重启即可生效:
# export ONESTEP_WEBHOOK_PORT=9000
# export ONESTEP_WORKERS=5
# python main.py


# 示例 3: JSON 配置文件
# config.json:
# {
#   "webhook": {
#     "host": "0.0.0.0",
#     "port": 9000
#   }
# }
#
# 使用:
# config = Config(config_file="config.json")


# 示例 4: 动态配置
config = Config()

# 可以在运行时设置配置（仅对当前进程有效）
config.set("custom.key", "value")

# 获取配置值
value = config.get("custom.key")
print(f"配置值: {value}")


if __name__ == '__main__':
    step.start(block=True)
