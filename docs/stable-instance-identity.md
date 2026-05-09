# OneStep 稳定实例身份指南

这份文档说明 `onestep` 如何决定 `instance_id`，怎样让它在重启后保持稳定，以及不同部署形态下应该怎么配置。

## 1. 哪些标识会稳定，哪些不会

- `instance_id`：控制面里看到的逻辑工作实例标识，应该尽量稳定
- `session_id`：每次进程启动后重新建立的 WS 会话标识，不会固定
- `runtime.started_at` 和 `pid`：当前进程信息，每次重启都会变化

正确的目标是：

- 同一个逻辑 worker 重启后继续使用同一个 `instance_id`
- 每次启动都拿到一个新的 `session_id`
- 控制面仍然能看出这是一次新的进程启动，而不是老进程还活着

## 2. `instance_id` 的解析顺序

`ControlPlaneReporterConfig.from_env()` 按下面顺序解析实例身份：

1. `ONESTEP_INSTANCE_ID`
2. `ONESTEP_REPLICA_KEY`
3. `ONESTEP_STATE_DIR` 里的本地身份状态

也就是说：

- 只要设置了 `ONESTEP_INSTANCE_ID`，它永远优先
- 如果没设置 `ONESTEP_INSTANCE_ID`，但设置了 `ONESTEP_REPLICA_KEY`，`onestep` 会基于 `service_name + environment + replica_key` 生成一个确定性的 UUIDv5
- 如果前两者都没设置，`onestep` 会从本地状态目录里的 `identity.json` 读取 `instance_id`，第一次启动时才新建

## 3. 后续如何固定 `instance_id`

后面固定 `instance_id`，可以按场景选下面 3 种方式。

### 3.1 方式一：持久化 `ONESTEP_STATE_DIR`

适用场景：

- 单机单进程 worker
- `systemd`、supervisor、长期运行的 VM 进程

工作原理：

- `onestep` 会在状态目录里保存 `identity.json`
- 重启后会复用同一个 `instance_id`
- `heartbeat_sequence` 和 `sync_sequence` 也会从这个文件继续累加

默认状态目录：

- `~/.onestep/control-plane-state/<environment>/<service_name>`
- 设置了 `ONESTEP_REPLICA_KEY` 时，会变成 `~/.onestep/control-plane-state/<environment>/<service_name>/<replica_key>`

示例：

```bash
export ONESTEP_ENV=prod
export ONESTEP_SERVICE_NAME=billing-sync
export ONESTEP_STATE_DIR=/var/lib/onestep/billing-sync
```

注意：

- 这个目录必须在重启后还在
- 不要让两个活着的 worker 同时使用同一个状态目录
- 如果两个活进程共用一个状态目录，启动时会因为身份锁直接失败

这是单 worker 场景下最推荐的方式。

### 3.2 方式二：设置 `ONESTEP_REPLICA_KEY`

适用场景：

- 同一个服务有多个 worker 副本
- Kubernetes StatefulSet
- 固定 worker 槽位，比如 `worker-0`、`worker-1`

工作原理：

- `onestep` 根据服务名、环境和副本 key 生成稳定 UUID
- 相同的 `replica_key` 永远映射到相同的 `instance_id`
- 不同的 `replica_key` 会得到不同的 `instance_id`

示例：

```bash
export ONESTEP_ENV=prod
export ONESTEP_SERVICE_NAME=billing-sync
export ONESTEP_REPLICA_KEY=worker-0
```

StatefulSet 示例：

```bash
export ONESTEP_REPLICA_KEY="${HOSTNAME##*-}"
```

这个方式要求副本 key 本身是稳定的。比如 `worker-0` 就应该一直代表同一个逻辑副本。

不要把每次 rollout 都会变化的随机 pod 名直接当成稳定副本 key，除非你自己额外注入了稳定逻辑标识。

这是多副本场景下最推荐的方式。

### 3.3 方式三：显式设置 `ONESTEP_INSTANCE_ID`

适用场景：

- 测试
- 人工迁移
- 你必须手工钉死某一个 UUID

示例：

```bash
export ONESTEP_INSTANCE_ID=8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df
```

注意：

- 这是硬覆盖
- 所有用了同一个 `ONESTEP_INSTANCE_ID` 的进程，都会声称自己是同一个逻辑实例
- 不要把同一个显式 UUID 配给多个活着的副本

生产环境里，多副本更建议用 `ONESTEP_REPLICA_KEY`，不要手工给每个副本发一个固定 UUID。

## 4. 不同部署形态怎么配

### 4.1 单机单 worker

建议配置：

- 设置 `ONESTEP_ENV`
- 设置 `ONESTEP_SERVICE_NAME`
- 设置一个持久化的 `ONESTEP_STATE_DIR`

示例：

```bash
export ONESTEP_ENV=prod
export ONESTEP_SERVICE_NAME=billing-sync
export ONESTEP_STATE_DIR=/var/lib/onestep/billing-sync
```

结果：

- 重启后 `instance_id` 不变
- 控制面里仍然是同一个逻辑实例

### 4.2 同一台机器多个 worker

建议配置：

- `ONESTEP_SERVICE_NAME` 保持一致
- 每个进程都给不同的 `ONESTEP_REPLICA_KEY`
- 或者每个进程给不同的 `ONESTEP_STATE_DIR`

示例：

```bash
export ONESTEP_ENV=prod
export ONESTEP_SERVICE_NAME=billing-sync
export ONESTEP_REPLICA_KEY=worker-2
export ONESTEP_STATE_DIR=/var/lib/onestep/billing-sync/worker-2
```

结果：

- 每个 worker 都有自己稳定的逻辑实例身份
- 不会发生锁冲突

### 4.3 Kubernetes StatefulSet

建议配置：

- 用稳定 ordinal 作为 `ONESTEP_REPLICA_KEY`
- `ONESTEP_SERVICE_NAME` 和 `ONESTEP_ENV` 保持稳定

如果 pod 名是 `billing-sync-0`、`billing-sync-1`、`billing-sync-2`，推荐在启动脚本里把 ordinal 提取出来再传给 `ONESTEP_REPLICA_KEY`。

示例：

```bash
export ONESTEP_REPLICA_KEY="${HOSTNAME##*-}"
```

这样 `0`、`1`、`2` 会分别对应固定逻辑副本。

### 4.4 Kubernetes Deployment

建议配置：

- 不要依赖随机 pod 名来固定实例身份
- 只有在你自己能分配稳定逻辑副本编号时，才使用 `ONESTEP_REPLICA_KEY`
- 如果每次替换 pod 都应该被视为新逻辑 worker，那就接受新的 `instance_id`

如果你在 Deployment 里也想让某个逻辑 worker 跨重启保持同一个 `instance_id`，你需要自己提供稳定副本分配机制，而不是依赖平台自动生成的临时名字。

## 5. 怎么确认当前是不是已经固定住了

先启动一次 worker，然后看状态文件：

```bash
cat ~/.onestep/control-plane-state/prod/billing-sync/identity.json
```

重点字段：

- `instance_id`
- `heartbeat_sequence`
- `sync_sequence`
- `created_at`
- `updated_at`

重启之后应该看到：

- `instance_id` 不变
- `heartbeat_sequence` 持续增加
- `sync_sequence` 持续增加
- 控制面里出现新的 session，但还是同一个逻辑实例

## 6. 常见问题

### 6.1 为什么重启后 `instance_id` 变了

常见原因：

- `ONESTEP_STATE_DIR` 没有持久化
- `ONESTEP_SERVICE_NAME`、`ONESTEP_ENV` 或 `ONESTEP_REPLICA_KEY` 变了
- 你跑在临时文件系统里，而且没有显式覆盖身份来源

处理方式：

- 使用持久化的 `ONESTEP_STATE_DIR`
- 或者设置稳定的 `ONESTEP_REPLICA_KEY`
- 或者显式设置 `ONESTEP_INSTANCE_ID`

### 6.2 为什么启动时报 identity lock 错误

原因：

- 两个活着的进程在抢同一个状态目录

处理方式：

- 每个进程使用不同的 `ONESTEP_STATE_DIR`
- 或者每个进程使用不同的 `ONESTEP_REPLICA_KEY`

### 6.3 为什么控制面里出现了多个逻辑实例

原因：

- worker 重启时没有稳定身份来源
- 或者不同副本实际拿到的身份输入不一致

排查顺序：

- 看 `ONESTEP_INSTANCE_ID`
- 看 `ONESTEP_REPLICA_KEY`
- 看 `ONESTEP_STATE_DIR`
- 看状态目录是否真的持久化
- 看同一个逻辑副本是否一直使用同一套输入

## 7. 给你的实际建议

可以直接按下面规则执行：

- 单机单 worker：固定 `ONESTEP_STATE_DIR`
- 多副本 worker：固定 `ONESTEP_REPLICA_KEY`
- 必须指定某个 UUID：设置 `ONESTEP_INSTANCE_ID`
- 不要让两个活着的 worker 共用同一个状态目录
- 不要让多个活着的副本共用同一个显式 `ONESTEP_INSTANCE_ID`
