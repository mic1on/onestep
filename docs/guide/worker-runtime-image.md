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

## 挂载工作区

```bash
docker run --rm \
  -e ONESTEP_TARGET=/workspace/worker.yaml \
  -v "$PWD:/workspace" \
  ghcr.io/mic1on/onestep-worker:1.4.2
```

启动流程：

1. 把 `/workspace` 和 `/workspace/src` 加入 `PYTHONPATH`
2. 如果存在 `/workspace/requirements.txt`，安装其中依赖
3. 否则如果存在 `/workspace/pyproject.toml`，安装当前项目
4. 运行 `onestep check "$ONESTEP_TARGET"`
5. 运行 `onestep run "$ONESTEP_TARGET"`

镜像内置 `onestep[all]`、`onestep-mq`、`onestep-mysql`、`onestep-redis` 和 `onestep-sqs`。如果 YAML 使用额外插件资源类型，确保 `requirements.txt` 或 `pyproject.toml` 包含对应插件，例如 `onestep-feishu-bitable`。

## 派生镜像

```dockerfile
FROM ghcr.io/mic1on/onestep-worker:1.4.2

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
| `onestep check` 失败 | 在本地运行同一个 target，先修复 YAML 或导入错误 |

## 下一步

- [生产部署](/guide/deploy) - systemd 与 CLI 部署
- [YAML 任务定义](/yaml-task-definition) - 编写 worker.yaml
- [连接器](/broker/) - 选择插件资源类型
