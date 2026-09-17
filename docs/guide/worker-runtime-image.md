---
title: Worker Runtime Image | 指南
outline: deep
---

# Worker Runtime Image

onestep 提供官方 worker runtime image，适合以 YAML 为入口运行 worker。镜像会在启动时安装工作区依赖，先执行 `onestep check`，再执行 `onestep run`。

## 必需环境变量

| 变量 | 说明 |
|---|---|
| `ONESTEP_TARGET` | YAML 文件路径或 Python import target |
| `WORKSPACE_DIR` | 工作区路径，默认 `/workspace` |

镜像入口脚本读取的全部变量见 [环境变量](/guide/environment-variables#worker-runtime-image)。

## 挂载工作区

```bash
docker run --rm \
  -e ONESTEP_TARGET=/workspace/worker.yaml \
  -v "$PWD:/workspace" \
  ghcr.io/mic1on/onestep-worker:1.12.0
```

启动流程：

1. 把 `/workspace` 和 `/workspace/src` 加入 `PYTHONPATH`
2. 如果存在 `/workspace/requirements.txt`，安装其中依赖
3. 否则如果存在 `/workspace/pyproject.toml`，安装当前项目
4. 运行 `onestep check "$ONESTEP_TARGET"`
5. 运行 `onestep run "$ONESTEP_TARGET"`

镜像内置 `onestep[all]`，并安装常用插件包，包括 RabbitMQ、Redis、MySQL、PostgreSQL、SQS、Cloudflare Queues、Kafka、MongoDB、Elasticsearch/OpenSearch、ClickHouse 和 control-plane reporter。若 YAML 使用额外插件资源类型，确保 `requirements.txt` 或 `pyproject.toml` 包含对应插件，例如 `onestep-feishu-bitable`。

### 工作区里有 pyproject.toml 时

入口脚本已把 `/workspace` 加入 `PYTHONPATH`，`handler.ref` 引用的模块无需安装即可导入；`pyproject.toml` 只用来声明额外依赖。但注意第 3 步会执行 `pip install /workspace`，把整个工作区当作一个 Python 项目构建。若工作区根目录平铺了多个顶层模块（如 `handler.py` 和 `client.py`），setuptools 的自动发现会拒绝构建：

```text
error: Multiple top-level modules discovered in a flat-layout: ['handler', 'client'].
```

任选其一修复：

1. **改用 `requirements.txt`（推荐）**：入口脚本优先安装它，纯依赖安装不触发项目构建。
2. **在 `pyproject.toml` 中显式声明不打包任何模块**：

   ```toml
   [tool.setuptools]
   packages = []
   py-modules = []
   ```

3. **确有需要安装的包**：把模块收进带 `__init__.py` 的包目录，并用 `[tool.setuptools.packages.find]` 的 `include` 指定发现范围。

## 派生镜像

```dockerfile
FROM ghcr.io/mic1on/onestep-worker:1.12.0

WORKDIR /workspace
COPY . /workspace
ENV ONESTEP_TARGET=/workspace/worker.yaml
```

构建并运行：

```bash
docker build -t my-worker .
docker run --rm my-worker
```

## 排查

| 现象 | 处理 |
|---|---|
| `ONESTEP_TARGET is required` | 设置 `ONESTEP_TARGET` |
| `target file is not readable` | 检查挂载路径、`WORKSPACE_DIR` 和目标文件 |
| 依赖安装失败 | 检查 `requirements.txt` 或 `pyproject.toml` |
| `Multiple top-level modules discovered in a flat-layout` | 工作区根目录平铺了多个顶层模块；改用 `requirements.txt`，或在 `pyproject.toml` 中显式声明 `packages`（见上文） |
| `onestep check` 失败 | 在本地运行同一个 target，先修复 YAML 或导入错误 |

## 下一步

- [生产部署](/guide/deploy) - systemd 与 CLI 部署
- [YAML 任务定义](/yaml-task-definition) - 编写 worker.yaml
- [连接器](/broker/) - 选择插件资源类型
