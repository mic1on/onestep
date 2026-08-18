# YAML 任务定义

`onestep` 把 YAML 作为任务定义和装配层：

- YAML 定义应用、资源、hooks、任务运行时策略，以及要调用的 Python 入口。
- transform、校验、富化和自定义 hooks 等业务逻辑仍由 Python 负责。

## 设计边界

YAML 负责：

- `app`：名称、全局配置、关闭超时、状态存储绑定、框架日志级别、失败捕获策略
- `reporter`：可选的 control-plane 遥测接线，通过 `onestep[control-plane]`
- `resources`：具名运行时对象及其依赖
- `hooks`：应用级 startup、shutdown 和事件观察者
- `tasks`：source、emit、死信、重试、超时、并发、handler、任务配置、任务 hooks

YAML 不定义：

- 内联 transform DSL
- 工作流图
- 表达式引擎
- 内嵌业务逻辑

YAML 可以按名称引用条件 Sink 路由的 Python 谓词可调用对象，以及面向单个 Sink 的 Python payload transform，但业务逻辑仍然写在 Python 中。

## 严格检查（Strict Check）

当你希望 YAML 表现得像一份真正的契约而不是宽松加载器时，使用严格检查：

```bash
onestep check --strict worker.yaml
```

strict 模式用于尽早暴露配置漂移：

- 未知的顶层字段
- 未知的 task、hook、reporter 和 resource 字段
- 存在时非法的 `apiVersion` / `kind` 取值
- 旧版顶层 app 字段与 `app:` 段的静默混用
- YAML 开启框架日志控制时非法的 `app.logging.level` 取值
- 非法的条件 `emit` 路由和 per-Sink 绑定形状

## 框架日志

纯 YAML worker 可以直接设置 `onestep` logger 命名空间级别：

```yaml
app:
  name: hello-worker
  logging:
    level: DEBUG
```

说明：

- 只设置 `onestep` logger 命名空间
- 不配置 root logger、handlers 或 formatters
- `DEBUG` 会打开底层框架日志，例如 Sink 发送成功
- 显式提供 `onestep run --log-level LEVEL` 时会覆盖该值
- 没有 CLI 覆盖时，`onestep run` 保留该值并输出到 stdout

对于长期维护的配置，建议加上：

```yaml
apiVersion: onestep/v1alpha1
kind: App
```

## 失败捕获

除非配置了 `app.failure_capture`，失败捕获默认关闭：

```yaml
app:
  name: billing-sync
  failure_capture:
    directory: ./captures
    mode: terminal
    max_bytes: 1048576
    redact_paths:
      - /body/customer/token
      - /meta/authorization
```

| 字段 | 必填/默认值 | 含义 |
| --- | --- | --- |
| `directory` | 必填 | 私有、原子写入的捕获文件目录。 |
| `mode` | `terminal` | `terminal` 只捕获最终失败；`all` 还捕获可重试的尝试。 |
| `max_bytes` | `1048576` | 正数的最大编码文件大小。超限的捕获会显式失败。 |
| `redact_paths` | `[]` | 逻辑 `/body` 和 `/meta` 文档下的 JSON Pointer 路径。 |

除配置的 pointer 外，已知的敏感键名也会递归脱敏。捕获文件使用带版本的 `onestep/envelope-capture` schema，保留 JSON 标量/容器，以及 datetime、UUID、bytes、Decimal、enum、tuple/namedtuple、set 和 frozenset 值。回放时 enum 和 namedtuple 类型必须保持可导入。

该格式不会把不支持的值字符串化。在 `mode: all` 下，包含不支持自定义值的尝试会记录捕获编码错误且不写文件；任务重试/死信行为保持不变。这样可以避免产生看起来可回放、实际已丢失类型信息的记录。

使用以下命令回放有效捕获：

```bash
onestep task replay worker.yaml --task sync_billing --envelope captures/failure.json
```

回放前会校验捕获 schema/版本以及 app/task 身份。除非显式指定 `--send`，Sink I/O 保持关闭。

## 真实项目结构

当团队真正采用 YAML 任务定义时，推荐的结构依然很小：

```text
your-project/
├── pyproject.toml
├── worker.yaml
└── src/
    └── your_worker/
        ├── tasks.py
        ├── transforms.py
        └── hooks.py
```

该示例已放在仓库的 `example/yaml_project/`。

规则不变：

- `worker.yaml` 定义运行时装配
- `tasks/` 定义 handlers
- `transforms/` 存放业务 transform
- `hooks.py` 可选，只放生命周期或旁路观察者逻辑

如果想直接得到这个结构，可以用脚手架命令生成：

```bash
onestep init your-project
```

`init` 刻意生成最小的可运行工程。默认不添加 reporter 配置、hook 模块、额外 hooks 或更多 YAML 结构。

在仓库根目录执行：

```bash
PYTHONPATH=src python -m onestep.cli check example/yaml_project/worker.yaml
PYTHONPATH=src python -m onestep.cli run example/yaml_project/worker.yaml
```

## 推荐演进路径

从能跑起来的最小结构开始，只在任务真正需要时才增加字段。

### 第 1 阶段：最小任务

```yaml
app:
  name: hello-worker
  logging:
    level: DEBUG

resources:
  tick:
    type: interval
    minutes: 5
    immediate: true

tasks:
  - name: hello
    source: tick
    handler:
      ref: worker.tasks.main:hello
```

这是默认的心智模型：

- 一个 app
- 一个 source
- 一个 handler
- 没有 hooks
- 没有额外配置

### 第 2 阶段：添加透传 Sink

如果任务只是把进入的 payload 转发到一个或多个 Sink，可以省略 `handler`。运行时会使用透传 handler，原样返回 source payload。

```yaml
app:
  name: event-forwarder

resources:
  incoming:
    type: memory

  notify:
    type: http_sink
    url: "https://example.com/hooks/events"
    headers:
      X-Api-Key: "${NOTIFY_TOKEN}"

tasks:
  - name: forward_events
    source: incoming
    emit: notify
```

strict 模式仍要求每个任务定义 `handler` 或非空 `emit` 之一。当 payload 需要 transform、校验、签名或富化时，使用 Python handler。

### 第 3 阶段：添加 Sink 和运行时策略

```yaml
app:
  name: user-sync

resources:
  users_source:
    type: mysql_incremental
    connector: mysql_main
    table: users
    key: id
    cursor: [updated_at, id]

  users_sink:
    type: mysql_table_sink
    connector: mysql_main
    table: dw_users
    mode: upsert
    keys: [id]

  mysql_main:
    type: mysql
    dsn: "${MYSQL_DSN}"

tasks:
  - name: sync_users
    source: users_source
    emit: [users_sink]
    handler:
      ref: worker.tasks.users:sync_users
    concurrency: 4
    timeout_s: 120
    retry:
      type: max_attempts
      max_attempts: 5
      delay_s: 10
```

`mysql_table_sink` 字段：

| 字段 | 必填/默认值 | 含义 |
| --- | --- | --- |
| `type` | 必填：`mysql_table_sink` | 资源类型。 |
| `connector` | 必填 | 引用一个 `mysql` connector。 |
| `table` | 必填 | 非空且已存在的表名。 |
| `mode` | `insert` | `insert` 或 `upsert`。 |
| `keys` | `upsert` 必填 | 用于检测冲突的唯一键列。 |
| `update_columns` | 可选 | 仅 `upsert`：冲突时更新的列白名单。默认为除 `keys` 外的所有 payload 列。 |
| `update_expr` | 可选 | 仅 `upsert`：列到原生 SQL 表达式的映射，冲突时渲染（例如 `updated_at: NOW(6)`）。 |
| `serialize_json` | `auto` | `auto`、`always` 或 `never`。`auto` 时，list/dict 类型的 payload 值会 JSON 序列化，除非目标列是 JSON 类型。 |

在 `upsert` 模式下，冲突时只重写 `update_columns` 选中的字段；未设置 `update_columns` 时，重写所有非键字段。设置 `update_columns: []` 会完全禁用 payload 更新，只保留 `update_expr` 条目（例如 `updated_at=NOW(6)`）在冲突时执行。`update_expr` 的值会渲染为原生 SQL 表达式。list 或 dict 类型的 payload 值在绑定前会序列化为 JSON 字符串，除非列类型是 JSON（`auto`）或显式关闭序列化（`never`）。

### 第 4 阶段：添加条件 Sink 路由

`emit` 条目可以混合无条件 Sink 和条件路由映射。YAML 只声明谓词可调用对象和目标 Sink；条件由 Python 求值。

```yaml
tasks:
  - name: route_users
    source: users_source
    emit:
      - audit_sink
      - when:
          ref: worker.routing:is_active_user
          params:
            status_field: status
        then: active_user_sink
        otherwise: inactive_user_sink
    handler:
      ref: worker.tasks.users:normalize_user
```

谓词可调用对象可以接收 `ctx`、`payload` 和 `result` 位置参数，也可以接收来自 `when.params` 的关键字参数。

```python
def is_active_user(ctx, payload, result, *, status_field: str) -> bool:
    return result.get(status_field) == "active"
```

规则：

- `when` 是 ref 字符串或 `{ref, params}` 映射。
- `then` 是 Sink 名称、Sink 名称列表，或 emit 绑定映射列表（`{sink, transform}`），如下文 Per-Sink Payload Transform 所示。
- `otherwise` 可选；省略时，谓词为假则跳过该路由。
- 各个 `emit` 条目相互独立、按顺序求值。
- 单个路由内只会选择 `then` 或 `otherwise` 之一。
- 谓词抛出的异常属于任务失败，走任务重试/死信策略。
- 后续路由或 Sink 失败时，已完成的 Sink 发送不会回滚。

### Per-Sink Payload Transform

当选中的 Sink 需要不同的 payload 形状时，使用绑定。YAML 声明静态拓扑，Python transform 负责 payload 投影。

~~~yaml
tasks:
  - name: extract_entities
    source: entity_events
    emit:
      - sink: entity_callback
      - sink: downstream_meta
        transform: worker.transforms:to_meta_row
    handler:
      ref: worker.tasks:extract_entities
~~~

sink 精确指向一个 Sink 资源。transform 可选；没有它时，该绑定原样接收 handler 结果。transform 是一个 Python 可调用对象，接收 ctx、原始 source payload 和 handler 结果；可以是同步或 async，返回发给该 Sink 的 body。

transform 的取值是 ref 字符串（`transform: worker.transforms:to_meta_row`），或当可调用对象需要调用时关键字参数时的 `{ref, params}` 映射；两种形式都可用于普通绑定和 `then`/`otherwise` 分支内部。

~~~python
async def to_meta_row(ctx, payload, result):
    return {
        "id": result["document_id"],
        "address": payload["address"],
    }


def to_prefixed_row(ctx, payload, result, *, prefix: str):
    return {"id": f"{prefix}:{result['document_id']}"}
~~~

当 transform 需要参数时，使用带 `params` 的映射形式；条目会成为调用时关键字参数：

~~~yaml
tasks:
  - name: extract_entities
    source: entity_events
    emit:
      - sink: downstream_meta
        transform:
          ref: worker.transforms:to_prefixed_row
          params:
            prefix: bidding
    handler:
      ref: worker.tasks:extract_entities
~~~

OneStep 会按 YAML 顺序求值所有选中的 transform，然后才发送到任何 Sink。如果某个 transform 失败，不会发送任何已配置的 Sink 输出，任务走正常的重试/死信策略。一旦开始分发，写入保持 at-least-once：后续 Sink 失败不会回滚先前的写入，因此在重复敏感的场景下每个目的地必须幂等。

绑定映射只能包含 sink 和 transform；不能与 when、then 或 otherwise 在同一条目中混用。绑定可以出现在条件路由的 `then` 和 `otherwise` 分支内，因此分支里的每个 Sink 都可以收到不同的 transform 后 payload。

```yaml
tasks:
  - name: extract_entities
    source: entity_events
    emit:
      - sink: entity_callback
      - when: worker.tasks:has_bidding_id
        then:
          - sink: meta_sink
            transform: worker.transforms:to_meta_row
          - sink: rows_sink
            transform: worker.transforms:to_bidding_row
        otherwise:
          - sink: fallback_sink
            transform: worker.transforms:to_fallback_row
    handler:
      ref: worker.tasks:extract_entities
```

顶层 `emit` 列表支持相同的条目形状：普通 Sink 名称，以及带可选 `transform` 的绑定映射。

```yaml
emit:
  - audit_sink
  - when: worker.routing:is_active
    then:
      - active_sink
      - sink: metric_sink
        transform: worker.transforms:to_metric
```

### 第 5 阶段：添加任务配置

把需要在运行时通过 `ctx.task_config` 可见的任务定义数据放进 `tasks[].config`。

```yaml
tasks:
  - name: sync_users
    source: users_source
    emit: [users_sink]
    config:
      dry_run: false
      target_table: dw_users
    handler:
      ref: worker.tasks.users:sync_users
      params:
        mode: upsert
```

经验法则：

- `handler.params`：Python 函数的调用时参数
- `task.config`：运行时和 handler 可以读取的任务定义数据

### 第 6 阶段：添加 Hooks

只有当任务装配或生命周期行为无法放进主 handler 时，才添加 hooks。

```yaml
hooks:
  startup:
    - ref: worker.lifecycle:on_startup
  shutdown:
    - ref: worker.lifecycle:on_shutdown

tasks:
  - name: sync_users
    source: users_source
    emit: [users_sink]
    handler:
      ref: worker.tasks.users:sync_users
    hooks:
      before:
        - ref: worker.task_hooks:before_sync_users
      on_failure:
        - ref: worker.task_hooks:on_sync_users_failed
```

### 第 7 阶段：添加 Control-Plane Reporter

只有需要 control-plane 遥测时才使用 control-plane reporter 插件。从最小配置开始：

```bash
pip install 'onestep[control-plane]'
```

```yaml
reporter: true
```

含义是：

- 加载 `onestep-control-plane` reporter 插件
- 从环境变量解析 `base_url` 和 `token`
- `service_name` 默认取 `app.name`

如果需要显式覆盖，保持最小化，字段名与 `ControlPlaneReporterConfig` 保持一致：

```yaml
reporter:
  base_url: https://control-plane.example.com
  token: ${ONESTEP_CONTROL_PLANE_TOKEN}
  service_name: billing-sync-worker
  service_description: Synchronizes billing data into the warehouse
```

- `service_description` 是可选的服务级元数据，由 control plane 展示。
- 也可以通过 `ONESTEP_SERVICE_DESCRIPTION` 提供。
- 任务级 `tasks[].description` 是独立的，描述单个任务。

### 第 8 阶段：完整装配示例

```yaml
apiVersion: onestep/v1alpha1
kind: App

app:
  name: user-sync
  shutdown_timeout_s: 30
  state: app_state
  config:
    region: cn

reporter: true

resources:
  mysql_main:
    type: mysql
    dsn: "${MYSQL_DSN}"

  app_state:
    type: mysql_state_store
    connector: mysql_main
    table: onestep_state

  cursor_users:
    type: mysql_cursor_store
    connector: mysql_main
    table: onestep_cursor

  users_source:
    type: mysql_incremental
    connector: mysql_main
    table: users
    key: id
    cursor: [updated_at, id]
    state: cursor_users
    state_key: users-sync

  users_sink:
    type: mysql_table_sink
    connector: mysql_main
    table: dw_users
    mode: upsert
    keys: [id]

  notify_api:
    type: http_sink
    url: "${NOTIFY_URL}"
    headers:
      Authorization: "Bearer ${NOTIFY_TOKEN}"
    success_statuses: [200, 202]

  audit_stream:
    type: redis_stream
    connector: redis_main
    stream: audit:user_sync
    group: onestep

  redis_main:
    type: redis
    url: "${REDIS_URL:redis://localhost:6379}"

  users_dead:
    type: redis_stream
    connector: redis_main
    stream: dead_letter:user_sync
    group: onestep

hooks:
  startup:
    - ref: worker.lifecycle:on_startup
      params:
        preload_cache: true
  shutdown:
    - ref: worker.lifecycle:on_shutdown
  events:
    - ref: worker.observers:metrics_handler
    - ref: worker.observers:structured_logger

tasks:
  - name: sync_users
    description: Sync incremental users into DW
    source: users_source
    emit: [users_sink, audit_stream, notify_api]
    dead_letter: [users_dead]
    config:
      target_table: dw_users
      dry_run: false
    metadata:
      owner: data-platform
      tags: [users, mysql]
    handler:
      ref: worker.tasks.users:sync_users
      params:
        mode: upsert
    hooks:
      before:
        - ref: worker.task_hooks:before_sync_users
      after_success:
        - ref: worker.task_hooks:after_sync_users
      on_failure:
        - ref: worker.task_hooks:on_sync_users_failed
    concurrency: 4
    timeout_s: 120
    retry:
      type: max_attempts
      max_attempts: 5
      delay_s: 10
```

## Python 侧

业务工程主要编写 handlers、transforms 和可选的 hooks。

```python
# worker/transforms/users.py
def normalize_user(payload: dict, *, region: str) -> dict:
    return {
        "id": payload["id"],
        "name": payload["name"].strip(),
        "region": region,
    }
```

```python
# worker/tasks/users.py
from worker.transforms.users import normalize_user


async def sync_users(ctx, payload, *, mode: str):
    row = normalize_user(payload, region=ctx.config["region"])

    if ctx.task_config.get("dry_run"):
        ctx.logger.info("dry run", extra={"payload": row})
        return None

    row["mode"] = mode
    return row
```

## 运行时访问

Handler 和任务 hooks 可以使用：

- `ctx.config`：来自 `app.config` 的应用级配置
- `ctx.task_config`：来自 `tasks[].config` 的任务级配置
- `ctx.task.config`：task spec 上的同一份任务配置
- `ctx.resources`：来自 `resources` 的具名运行时对象
- `ctx.state`：按任务命名空间隔离的状态

应用 hooks 可以使用：

- `app.resources`：来自 `resources` 的具名运行时对象
- `app.tasks`：已加载的 task spec
- `app.config`：应用级配置

## Hook 签名

`onestep` 会依据可调用对象的签名截断位置参数，因此 hooks 可以按需选择要接收的上下文。

支持的应用级 hooks：

- `startup`：`func(app)` 或 `func()`
- `shutdown`：`func(app)` 或 `func()`
- `events`：`func(event)` 或 `func()`

支持的任务级 hooks：

- `before`：`func(ctx, payload)`、`func(ctx)` 或 `func()`
- `after_success`：`func(ctx, payload, result)`、`func(ctx, payload)`、`func(ctx)` 或 `func()`
- `on_failure`：`func(ctx, payload, failure)`、`func(ctx, payload)`、`func(ctx)` 或 `func()`

Hook `params` 会在运行时参数之后作为关键字参数传入。

## Hook 语义

- `before` 在 delivery 开始处理之后、`started` 事件发出之后运行。
- `after_success` 在 handler 成功返回之后、发送到 Sink 之前、`ack()` 之前运行。
- 条件 `emit.when` 谓词在 `after_success` 之后、Sink 发送和 `ack()` 之前运行。
- `on_failure` 在任务失败时运行，早于重试或死信决策生效。
- `on_failure` hooks 内部的失败只记录日志，不会替换原始任务失败。
- `timeout_s` 当前只作用于 async handler 本体；任务 hooks 不在该超时范围内。

## 资源说明

- `resources` 是具名运行时对象的首选顶层段。
- 旧版 `connectors`、`sources` 和 `sinks` 段仍可读取，并合并进同一个资源注册表。
- 资源在运行时通过 `app.resources` 和 `ctx.resources` 可用。

内置资源类型：

- `memory`
- `interval`
- `cron`
- `webhook`
- `http_sink`

strict 模式下，`memory` 资源必须设置正数 `maxsize`；这可以避免长期运行的 YAML worker 意外创建无界的进程内队列。定时 `interval` 和 `cron` 资源支持为 `overlap: queue` 设置 `max_queued_runs`，默认 `1000`。

`http_sink` 默认把任务结果以 JSON 发送。只有需要重塑出站 payload 时才配置 `body`。`url`、`headers`、`params` 以及配置的 `body` 取值可以通过 `&#123;&#123; ... &#125;&#125;` 变量引用 `body`、`payload`、`meta` 和 `attempts`。

插件资源类型：

- `onestep-elasticsearch`：`elasticsearch`、`elasticsearch_bulk_sink`
- `onestep-clickhouse`：`clickhouse`、`clickhouse_table_sink`
- `onestep-mysql`：`mysql`、`mysql_state_store`、`mysql_cursor_store`、`mysql_table_queue`、`mysql_incremental`、`mysql_table_sink`
- `onestep-mq`：`rabbitmq`、`rabbitmq_queue`
- `onestep-redis`：`redis`、`redis_stream`
- `onestep-sqs`：`sqs`、`sqs_queue`
- `onestep-kafka`：`kafka`、`kafka_topic`
- `onestep-feishu-bitable`：`feishu_bitable`、`feishu_bitable_incremental`、`feishu_bitable_table_sink`
- `onestep-mongodb`：`mongodb`、`mongodb_polling`、`mongodb_change_stream`、`mongodb_collection_sink`

### Elasticsearch 与 OpenSearch 资源

直接安装 `onestep-elasticsearch`，或使用 `pip install 'onestep[elasticsearch]'`。一个 connector 即可覆盖通用的 Elasticsearch/OpenSearch HTTP bulk 边界：

```yaml
resources:
  search:
    type: elasticsearch
    hosts: ["${SEARCH_URL}"]
    distribution: auto
    username: "${SEARCH_USERNAME}"
    password: "${SEARCH_PASSWORD}"
    verify_certs: true
    ca_certs: "${SEARCH_CA_FILE:-/etc/ssl/certs/ca-certificates.crt}"
    request_timeout_s: 30

  events:
    type: elasticsearch_bulk_sink
    connector: search
    index: events-v1
    operation: index
    id_field: event_id
    chunk_size: 500
    max_chunk_bytes: 5000000
    refresh: false
```

`elasticsearch` 字段：

| 字段 | 必填/默认值 | 含义 |
| --- | --- | --- |
| `type` | 必填：`elasticsearch` | 资源类型。 |
| `hosts` | 必填 | 非空的 HTTP(S) URL 字符串或字符串列表。 |
| `distribution` | `auto` | `auto`、`elasticsearch` 或 `opensearch`。 |
| `username` | 可选 | Basic 认证用户名；需要 `password`。 |
| `password` | 可选 | Basic 认证密码；需要 `username`；敏感信息。 |
| `api_key` | 可选 | API-key 凭证；敏感信息。 |
| `bearer_token` | 可选 | Bearer 凭证；敏感信息。 |
| `headers` | 可选 | 自定义 HTTP 头映射；敏感信息。 |
| `verify_certs` | `true` | 启用 TLS 证书校验。 |
| `ca_certs` | 可选 | CA 证书束路径。 |
| `client_cert` | 可选 | 客户端证书路径。 |
| `client_key` | 可选 | 客户端密钥路径；敏感信息。 |
| `request_timeout_s` | `10.0` | 正数的请求超时（秒）。 |

认证只能不配置，或只配置 Basic、API key、Bearer 中的一种。Basic 用户名/密码对算作一种模式。strict 模式会在不连接任何服务的情况下拒绝：不完整的 Basic 凭证、多种认证模式、非法 host scheme、未知字段以及非正数超时。

`elasticsearch_bulk_sink` 字段：

| 字段 | 必填/默认值 | 含义 |
| --- | --- | --- |
| `type` | 必填：`elasticsearch_bulk_sink` | 资源类型。 |
| `connector` | 必填 | 引用一个 `elasticsearch` connector。 |
| `index` | 必填 | 非空的静态目标索引。 |
| `operation` | `index` | `index` 或 `create`。 |
| `id_field` | 可选 | 复制到 `_id` 并保留在 `_source` 中的 payload 字段。 |
| `chunk_size` | `500` | 每次请求的最大 action 数（正数）。 |
| `max_chunk_bytes` | `5000000` | 每次请求序列化后 NDJSON 的最大字节数（正数）。 |
| `refresh` | `false` | `false`、`true` 或 `wait_for`。 |
| `pipeline` | 可选 | 静态 ingest pipeline 名称。 |

index 和 operation 是静态资源配置，不是按 payload 路由的字段。本版本没有注册 Elasticsearch/OpenSearch 搜索 source。

### ClickHouse 资源

直接安装 `onestep-clickhouse`，或使用 `pip install 'onestep[clickhouse]'`：

```yaml
resources:
  analytics:
    type: clickhouse
    dsn: "${CLICKHOUSE_DSN}"
    client_options:
      connect_timeout: 10
      send_receive_timeout: 30

  events:
    type: clickhouse_table_sink
    connector: analytics
    table: events
    columns: [event_id, occurred_at, kind, payload]
    batch_size: 1000
    settings:
      async_insert: 0
```

`clickhouse` 字段：

| 字段 | 必填/默认值 | 含义 |
| --- | --- | --- |
| `type` | 必填：`clickhouse` | 资源类型。 |
| `dsn` | 必填 | 非空的 ClickHouse 或 HTTP(S) DSN；敏感信息。 |
| `client_options` | 可选 | 传给 async client 创建的映射；敏感信息。 |

`clickhouse_table_sink` 字段：

| 字段 | 必填/默认值 | 含义 |
| --- | --- | --- |
| `type` | 必填：`clickhouse_table_sink` | 资源类型。 |
| `connector` | 必填 | 引用一个 `clickhouse` connector。 |
| `table` | 必填 | 非空且已存在的表名。 |
| `columns` | 可选 | 非空且唯一的列表，固定行列顺序。 |
| `batch_size` | `1000` | 每次 insert 的最大行数（正数）。 |
| `settings` | 可选 | 传给每次 insert 的映射。 |

配置了 `columns` 时，每一行必须恰好包含所有命名列、不得有其他列。未配置 `columns` 时，第一行固定插入顺序，后续行的键集合必须一致。如果启用 `settings.async_insert`，strict 模式还要求 `wait_for_async_insert: 1`；不允许 fire-and-forget 插入。

### MongoDB 资源

直接安装 `onestep-mongodb`，或使用 `pip install 'onestep[mongodb]'`。change stream 需要副本集或分片集群。下面的生产示例使用显式的持久化 PostgreSQL cursor store：

```yaml
resources:
  mongo:
    type: mongodb
    uri: "${MONGODB_URI}"
    database: app
    client_options:
      serverSelectionTimeoutMS: 10000

  cursor_db:
    type: postgres
    dsn: "${POSTGRES_DSN}"

  events_cursor:
    type: postgres_cursor_store
    connector: cursor_db
    table: onestep_cursor

  events_poll:
    type: mongodb_polling
    connector: mongo
    collection: events
    cursor: [updated_at, _id]
    filter:
      archived: false
    batch_size: 100
    poll_interval_s: 1
    state: events_cursor
    state_key: events-poll

  events_changes:
    type: mongodb_change_stream
    connector: mongo
    collection: events
    pipeline:
      - $match:
          operationType:
            $in: [insert, update, delete]
    full_document: updateLookup
    max_await_time_ms: 1000
    batch_size: 100
    poll_interval_s: 0.1
    state: events_cursor
    state_key: events-change-stream

  archive:
    type: mongodb_collection_sink
    connector: mongo
    collection: events_archive
    mode: upsert
    keys: [event_id]
    ordered: true
    batch_size: 1000
```

`mongodb` 字段：

| 字段 | 必填/默认值 | 含义 |
| --- | --- | --- |
| `type` | 必填：`mongodb` | 资源类型。 |
| `uri` | 必填 | 非空 MongoDB URI；敏感信息。 |
| `database` | 必填 | 非空数据库名。 |
| `client_options` | 可选 | 传给 `AsyncMongoClient` 的映射；敏感信息。 |

strict 模式会拒绝非确认的 `w=0` write concern。

`mongodb_polling` 字段：

| 字段 | 必填/默认值 | 含义 |
| --- | --- | --- |
| `type` | 必填：`mongodb_polling` | 资源类型。 |
| `connector` | 必填 | 引用一个 `mongodb` connector。 |
| `collection` | 必填 | 非空 collection 名。 |
| `cursor` | `[_id]` | 非空且唯一的字段列表；显式 `_id` 必须放最后。 |
| `filter` | 可选 | 与 keyset 谓词合并的查询映射。 |
| `projection` | 可选 | projection 映射。 |
| `batch_size` | `100` | 每次抓取的最大文档数（正数）。 |
| `poll_interval_s` | `1.0` | 空轮询之间的非负延迟。 |
| `state` | 可选 | cursor-store 资源引用。 |
| `state_key` | 可选 | 持久化 cursor key 覆盖。 |
| `initial_cursor` | 可选 | 仅在没有存储状态时使用的 JSON cursor。 |

未配置 `_id` 时，会把它追加为确定性的最终决胜字段。轮询是升序 keyset 遍历，不是 CDC：删除不可见，未推进 cursor 字段的更新可能漏掉。轮询 projection 必须原样保留所有生效的 cursor 字段，包括隐式的 `_id` 决胜字段。非法的 projection 会在资源构造时失败。

`mongodb_change_stream` 字段：

| 字段 | 必填/默认值 | 含义 |
| --- | --- | --- |
| `type` | 必填：`mongodb_change_stream` | 资源类型。 |
| `connector` | 必填 | 引用一个 `mongodb` connector。 |
| `collection` | 必填 | 非空 collection 名。 |
| `pipeline` | 可选 | 聚合阶段的 JSON 列表。 |
| `full_document` | `updateLookup` | 支持的 PyMongo full-document 选项。 |
| `max_await_time_ms` | `1000` | 正数的服务端等待时间。 |
| `batch_size` | `100` | 每次抓取的最大事件数（正数）。 |
| `poll_interval_s` | `0.1` | 空抓取后的非负延迟。 |
| `state` | 可选 | cursor-store 资源引用。 |
| `state_key` | 可选 | 持久化 resume-token key 覆盖。 |

change stream 发出完整的 MongoDB 原生变更事件。没有存储状态时，会从服务端当前位置开始，而不是回放 collection 历史。非法或过期的 resume token 会永久失败，需要运维显式重置。

`mongodb_collection_sink` 字段：

| 字段 | 必填/默认值 | 含义 |
| --- | --- | --- |
| `type` | 必填：`mongodb_collection_sink` | 资源类型。 |
| `connector` | 必填 | 引用一个 `mongodb` connector。 |
| `collection` | 必填 | 非空 collection 名。 |
| `mode` | `insert` | `insert` 或 `upsert`。 |
| `keys` | `upsert` 必填 | 非空且唯一的键字段列表。 |
| `ordered` | `true` | 保持有序 bulk-write 行为。 |
| `batch_size` | `1000` | 每次写入的最大文档数（正数）。 |

三个数据库 bulk Sink 都接受单个映射或非空映射序列，并等待每个后端 chunk 确认。重试可能重复已提交的 chunk；在重复敏感的场景下使用稳定 ID/键，或后端去重友好的 schema 设计。除非完整 payload 的回放可证明幂等，部分提交会被归类为 `UNCERTAIN`。

MongoDB 轮询和 change stream 开发时可以使用内存状态。生产环境的重启保证需要显式的持久化 `state` cursor store。通过通用 cursor store 存储时，resume token 和 cursor 值使用 BSON Extended JSON。

`kafka_topic` 可以用作 source、Sink 或两者。用作 source 时需要设置 `group_id`；插件会关闭 Kafka auto commit，只在 onestep 到达 `ack()` 或终态 `fail()` 之后提交 offset。

`feishu_bitable_incremental` 支持 `fallback_scan_page_limit`，用于限制飞书拒绝按 cursor 排序时的兜底扫描。默认为 `100` 页。

在 YAML 中使用插件资源类型前，先在 worker 环境安装对应的插件包。

已安装的包可以提供更多资源类型。包可以通过 `onestep.resources` entry point 组注册 YAML 资源：

```toml
[project.entry-points."onestep.resources"]
feishu_bitable = "onestep_feishu_bitable:register"
```

entry point 接收资源注册表并注册一个或多个资源处理器。只要该包安装在 worker 环境中，YAML 文件就可以使用其提供的 `type` 取值，无需修改 onestep 核心。

仓库在 `plugins/` 下包含各个插件包，各自有独立的 entry point 和发布流程。

## MySQL 到飞书 Insert 的批次控制

`mysql_incremental.batch_size` 限制单次 source 抓取返回的行数，运行时还会按任务可用并发进一步封顶。`feishu_bitable_table_sink.batch_size` 是飞书写入边界。`tasks[].concurrency` 是在途 delivery 的最大数量。三者相互独立；`tasks[].config.batch_size` 只是暴露给 `ctx.task_config` 的任意数据，不会对任何 connector 批处理。严格小写、持久化 cursor 的配置示例见 `example/mysql_feishu_insert.yaml`。
