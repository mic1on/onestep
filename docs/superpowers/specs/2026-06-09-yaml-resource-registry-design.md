# YAML Resource Registry 设计

## 摘要

将 YAML resource 的构建与 strict 校验从 `src/onestep/config.py` 中拆出，改为一个可扩展的 resource registry。内置 resource 不再通过 `config.py` 里的长 `if resource_type == ...` 分支处理，而是按 connector 领域拆成 `onestep.resources.*` 模块并注册到 registry。

外部连接器包可以通过 Python entry point 注册自己的 YAML resource 类型。用户安装插件包后，不需要修改 onestep core，就可以在 YAML 里使用插件提供的 `type`。

## 背景

Feishu Bitable source/sink 已经证明现有 `Source`、`Sink`、`Delivery`、control-plane descriptor 扩展点可以承载外部连接器。真正的扩展瓶颈在 YAML resource 构建层：

- `config.py` 维护 `_STRICT_RESOURCE_FIELDS`
- `config.py` 维护每种 resource 的 strict 细节校验
- `config.py` 维护 `_build_resource(...)` 的所有构建分支
- 新增 SaaS connector 会继续扩大 core 包表面积

这个设计解决 YAML resource 类型扩展问题，不改变 runtime task API 和 control-plane 协议。

## 目标

- 让外部包可以通过 entry point 注册 YAML resource 类型。
- 保留所有现有 resource type 名称和 YAML 行为。
- 保留 strict mode 的未知字段检查和跨字段校验能力。
- 将内置 resource 构建逻辑按领域拆出 `config.py`。
- 保持 `resources`、`connectors`、`sources`、`sinks` 四个 YAML section 的合并语义。
- 保持资源依赖解析、重复定义检查、循环依赖检测的现有行为。
- 保持直接 Python API 使用方式不变。

## 非目标

- 不在本次迁移中把 Feishu 从 core 包发布为独立包。
- 不要求插件 resource 必须继承新的基类；只要求注册 handler。
- 不改变 `Source`、`Sink`、`Delivery`、`OneStepApp` 的公共 API。
- 不改变 control-plane backend schema。
- 不把每个插件 helper 都导出到 root `onestep`。
- 不支持首版 YAML 显式加载本地插件模块；首版只支持 entry point 自动加载。

## 用户可见行为

现有 YAML 保持可用：

```yaml
resources:
  feishu:
    type: feishu_bitable
    app_id: "${FEISHU_APP_ID}"
    app_secret: "${FEISHU_APP_SECRET}"

  source_orders:
    type: feishu_bitable_incremental
    connector: feishu
    app_token: "${FEISHU_APP_TOKEN}"
    table_id: "tblxxx"
    cursor_field: 最后更新时间
```

外部插件包的目标用法：

```bash
pip install onestep-feishu
```

插件包声明：

```toml
[project.entry-points."onestep.resources"]
feishu = "onestep_feishu:register"
```

插件入口：

```python
from onestep.resource_registry import ResourceSpecHandler


def register(registry):
    registry.register_resource_type(
        ResourceSpecHandler(
            type="feishu_bitable",
            allowed_fields=frozenset({"type", "app_id", "app_secret"}),
            build=build_feishu_bitable,
            validate=validate_feishu_bitable,
        )
    )
```

用户不需要在 YAML 里写 `plugins` 或 Python module 路径。`load_yaml_app(...)` 和 `load_app_config(...)` 会在解析 resource 前加载 entry point。

## 架构

新增 `src/onestep/resource_registry.py`，提供稳定的注册 API：

```python
@dataclass(frozen=True)
class ResourceSpecHandler:
    type: str
    allowed_fields: frozenset[str] | None
    build: Callable[[ResourceBuildContext, Mapping[str, Any]], Any]
    validate: Callable[[ResourceValidationContext, Mapping[str, Any]], None] | None = None


class ResourceRegistry:
    def register_resource_type(self, handler: ResourceSpecHandler) -> None: ...
    def get_resource_handler(self, resource_type: str) -> ResourceSpecHandler | None: ...
    def handlers(self) -> Mapping[str, ResourceSpecHandler]: ...


def default_resource_registry() -> ResourceRegistry: ...
def register_resource_type(handler: ResourceSpecHandler) -> None: ...
def get_resource_handler(resource_type: str) -> ResourceSpecHandler | None: ...
def load_resource_plugins(registry: ResourceRegistry | None = None) -> None: ...
```

`ResourceBuildContext` 承载 YAML resource 构建所需能力：

- `name`: YAML resource key
- `type`: 规范化后的 resource type
- `field`: 错误信息用路径，例如 `resources.source_orders`
- `resolve(name)`: 解析另一个 resource，保留现有依赖解析与循环检测
- `resource_name(spec, fallback, key="name")`: 复用现有命名规则
- `require_string(...)`、`string_list(...)`、`mapping_value(...)`、`optional_ref(...)` 等 YAML helper
- `is_cursor_store(...)`、`is_state_store(...)` 等能力检测 helper

`ResourceValidationContext` 承载 strict 校验所需能力：

- `name`
- `type`
- `field`
- `require_string(...)`
- `string_value(...)`
- `require_non_empty_string_list(...)`
- `validate_positive_number(...)`
- `validate_non_negative_number(...)`
- `validate_positive_integer(...)`
- `validate_unknown_fields(...)`

helper 通过 context 暴露，避免插件 import `config.py` 里的私有函数。首版可以先把 helper 实现在 `resource_registry.py`，`config.py` 和内置 resource 模块共用同一套函数，减少行为漂移。

## 内置 Resource 模块拆分

新增 `src/onestep/resources/` 包：

- `__init__.py`: 提供 `register_builtin_resources(registry)`，集中调用各模块注册函数
- `memory.py`: `memory`
- `schedule.py`: `interval`、`cron`
- `webhook.py`: `webhook`
- `http.py`: `http_sink`
- `feishu.py`: `feishu_bitable`、`feishu_bitable_incremental`、`feishu_bitable_table_sink`
- `rabbitmq.py`: `rabbitmq`、`rabbitmq_queue`
- `redis.py`: `redis`、`redis_stream`
- `sqs.py`: `sqs`、`sqs_queue`
- `mysql.py`: `mysql`、`mysql_state_store`、`mysql_cursor_store`、`mysql_table_queue`、`mysql_incremental`、`mysql_table_sink`

每个模块只负责 YAML handler 注册，不改变 connector 类所在位置。举例：

- `onestep.connectors.feishu` 继续放 `FeishuBitableConnector`
- `onestep.resources.feishu` 只放 YAML 构建和 strict 校验 handler

这样可以避免 connector 实现和 YAML config 细节继续互相挤在同一个模块里。

## Config 数据流

`load_yaml_app(...)` 保持当前流程：

1. 读取 YAML
2. 展开环境变量
3. 调用 `load_app_config(...)`

`load_app_config(...)` 在 strict 校验前确保 registry 准备好：

1. `register_builtin_resources(default_resource_registry())`
2. `load_resource_plugins(default_resource_registry())`
3. strict 模式下调用 `validate_app_config(...)`
4. 创建 `OneStepApp`
5. `_build_resources(...)`
6. 绑定 resources、tasks、hooks、reporter

注册内置 resource 和加载插件都必须是幂等的。多次调用 `load_app_config(...)` 不应重复注册或重复执行同一个 entry point。

`_build_resources(...)` 保留现有职责：

- 收集四个 resource section
- 拒绝同名不同配置
- 递归解析依赖
- 检测循环依赖

`_build_resource(...)` 改为：

1. 读取并规范化 `type`
2. 从 registry 查找 handler
3. 找不到则抛出 `ValueError("unsupported resource type ...")`
4. 构造 `ResourceBuildContext`
5. 调用 `handler.build(ctx, spec)`

`_validate_resource_sections(...)` 改为：

1. 读取并规范化 `type`
2. 从 registry 查找 handler
3. 找不到则抛出 `ValueError("unsupported resource type ...")`
4. 如果 `handler.allowed_fields is not None`，先做未知字段检查
5. 如果 handler 提供 `validate`，调用自定义 strict 校验

## 插件加载策略

entry point group 固定为 `onestep.resources`。

加载规则：

- `load_resource_plugins(...)` 默认加载到全局默认 registry。
- 对同一个 `ResourceRegistry` 实例，每个 entry point 只加载一次。
- entry point callable 必须接受一个 `ResourceRegistry` 参数。
- 插件注册函数可以注册一个或多个 resource type。
- 插件加载失败时 fail fast，抛出带 entry point 名称的 `RuntimeError`，保留原始异常作为 cause。
- 未安装插件但 YAML 使用插件 resource type 时，继续报 `unsupported resource type`。
- `importlib.metadata.entry_points()` 需要兼容 Python 3.9 到 3.12 的返回值差异；优先使用 `select(group="onestep.resources")`，没有 `select` 时使用旧式 mapping fallback。

重复注册规则：

- 相同 `type` 注册两次且 handler 对象不同，抛出 `ValueError`。
- 相同 handler 重复注册视为幂等成功。
- 内置 resource 先注册，插件不能覆盖内置 resource type。

这种规则让错误尽早暴露，避免安装两个插件后静默覆盖 resource 行为。

## Strict 校验

strict mode 的行为保持不变：

- 不认识的 resource type 报错
- 不支持的字段报错
- 缺失必填字段报错
- 类型错误使用 `TypeError`
- 值不合法使用 `ValueError`

Feishu 这类有跨字段规则的 resource 使用 handler 自定义 `validate`：

- `feishu_bitable_table_sink` 的 `mode` 只能是 `upsert`、`create`、`update`
- `mode` 为 `upsert` 或 `update` 时必须提供非空 `match_fields`
- `user_id_type` 只能是 `open_id`、`union_id`、`user_id`

对于只需要字段白名单的 resource，可以只提供 `allowed_fields`，不提供 `validate`。

对于完全自定义校验的插件，`allowed_fields` 可以设为 `None`，由插件 `validate` 完全负责 strict 校验。但内置 resource 应优先使用 `allowed_fields`，保持错误信息一致。

## 错误处理

保持现有错误风格：

- resource section 不是 mapping：`TypeError("'resources' must be a mapping")`
- resource spec 不是 mapping：`TypeError("'resources.foo' must be a mapping")`
- 缺失 `type`：沿用 `"'type' must be a non-empty string"`
- 未知 resource type：`ValueError("unsupported resource type 'x' for resources.foo")`
- 构建时未知 resource type：`ValueError("unsupported resource type 'x' for resource 'foo'")`
- 循环依赖：沿用 `ValueError("cyclic resource reference detected: ...")`
- 插件加载失败：`RuntimeError("failed to load onestep resource plugin 'name'")`

插件 handler 抛出的异常不吞掉。这样错误能指向插件自己的校验或构建逻辑。

## 兼容性

兼容项：

- 现有 YAML resource type 名称不变。
- `resources`、`connectors`、`sources`、`sinks` 合并逻辑不变。
- 现有 Python API 不变。
- `onestep.__all__` 中已有导出不因为本次改动删除。
- control-plane descriptor 行为不变。
- optional dependency 行为不变；例如缺少 `aio-pika` 时仍由 RabbitMQ connector 在实际使用时报错。

需要注意的迁移风险：

- 现有测试会 monkeypatch `onestep.config.RabbitMQConnector`、`SQSConnector`、`MySQLConnector`。拆分后测试应改为 monkeypatch 对应 `onestep.resources.*` 模块里的 connector 引用，或者通过 registry 注入测试 handler。
- `config.py` 中的私有 helper 会迁移或转发到 `resource_registry.py`。内部调用点需要一次性收敛，避免出现两套校验逻辑。
- 内置注册必须在 strict 校验前完成，否则 strict mode 会误报所有 resource type 不支持。

## 测试策略

单元测试覆盖：

- registry 规范化 resource type，例如 `feishu-bitable`、`feishu.bitable`、`feishu_bitable` 归一。
- 注册同一 handler 幂等。
- 不同 handler 重复注册同一 type 报错。
- 未知 resource type 在 strict 和 build 阶段都给出清晰错误。
- fake entry point 可以注册新 resource type。
- entry point 加载幂等，不重复调用插件。
- entry point 抛错时包装为带插件名的 `RuntimeError`。
- plugin resource 的 `allowed_fields` 在 strict mode 生效。
- plugin resource 的自定义 `validate` 在 strict mode 生效。
- plugin resource 的 `build` 可以通过 `ctx.resolve(...)` 解析依赖。
- 循环依赖检测在 registry handler 下保持有效。

回归测试覆盖：

- 现有 `test_cli.py` YAML 构建测试继续通过。
- 现有 Redis YAML 测试继续通过。
- 现有 HTTP sink strict 测试继续通过。
- 现有 Feishu Bitable YAML strict 测试继续通过。
- 现有 packaging 测试继续证明 core import 不强制需要 optional dependencies。

文档测试或示例覆盖：

- `docs/yaml-task-definition.md` 更新 resource registry 和插件说明。
- Feishu 示例在 core 内置阶段保持原样。
- 插件示例展示 entry point 注册方式。

## 实施顺序

1. 新增 `resource_registry.py`，先放 registry、handler dataclass、context、helper。
2. 新增 `resources/` 包和 `register_builtin_resources(...)`。
3. 先迁移一个简单 resource，例如 `memory`，验证 config 与 strict 数据流。
4. 迁移 schedule、webhook、http。
5. 迁移 Feishu，确保复杂 strict 校验通过。
6. 迁移 RabbitMQ、Redis、SQS、MySQL。
7. 删除 `config.py` 中 `_STRICT_RESOURCE_FIELDS` 和 `_build_resource(...)` 长分支。
8. 增加 entry point 加载测试和插件 resource 测试。
9. 更新 YAML 文档。

实施过程中每一步都保持测试可运行。迁移完所有内置 resource 后再删除旧分支，避免中间状态破坏现有 YAML。

## 后续工作

registry API 稳定后，可以单独规划：

- 将 Feishu 拆到 `onestep-feishu`
- 增加 WeCom、DingTalk 等独立插件包
- 如果有明确需求，再设计 YAML 显式加载本地插件模块
- 如果多个插件共享复杂校验，再扩展公开 helper，而不是让插件依赖私有函数
