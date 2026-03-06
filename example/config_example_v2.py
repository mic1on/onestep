"""
使用 Config.from_config() 方法的示例

展示更优雅的配置管理方式
"""
from onestep import step, WebHookBroker, Config


# 示例 1: WebHookBroker 使用 from_config()
config = Config(config_file=".onestep.yml")

@step(
    from_broker=WebHookBroker.from_config(config),
    workers=config.get("workers.default", 1),
)
def handle_webhook(message):
    print(f"收到消息: {message}")


if __name__ == '__main__':
    step.start(block=True)


# 示例 2: 多个配置源

# 从文件加载
file_config = Config(config_file=".onestep.yml")

# 可以合并环境变量覆盖（Config 已内置支持）
# export ONESTEP_WEBHOOK_PORT=9000

# 使用配置
@step(
    from_broker=WebHookBroker.from_config(file_config),
)
def handle_webhook_v2(message):
    print(f"收到消息: {message}")


# 示例 3: 对比旧写法

# ❌ 旧写法（繁琐）
# @step(
#     from_broker=WebHookBroker(
#         path="/webhook",
#         host=config.get("webhook.host", "127.0.0.1"),
#         port=config.get("webhook.port", 8090),
#         api_key=config.get("webhook.api_key"),
#     ),
#     workers=config.get("workers.default", 1),
# )

# ✅ 新写法（简洁）
# @step(
#     from_broker=WebHookBroker.from_config(config),
#     workers=config.get("workers.default", 1),
# )
