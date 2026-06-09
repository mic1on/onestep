# Feishu Bitable 插件化设计

## 背景

Feishu Bitable 只覆盖飞书多维表格，不应占用 `feishu` 这样宽泛的命名，也不应继续作为 core 内置 connector 增加核心包体和维护边界。onestep core 已有 `onestep.resources` entry point 机制，可以让外部包注册 YAML resource handler。

本次调整采用同仓库插件开发模式：插件源码放在 `plugins/` 下，发布时仍可作为独立 Python 包安装。

## 目标

- 新增 repo-local 插件包 `plugins/onestep-feishu-bitable`。
- 插件包名为 `onestep-feishu-bitable`，Python import 包名为 `onestep_feishu_bitable`。
- 插件注册 YAML 类型：
  - `feishu_bitable`
  - `feishu_bitable_incremental`
  - `feishu_bitable_table_sink`
- core 不再导出或内置 Feishu Bitable connector。
- 不保留旧 `onestep.connectors.feishu`、`onestep.connectors.feishu_bitable`、`from onestep import FeishuBitableConnector` 兼容路径。

## 包结构

```text
plugins/onestep-feishu-bitable/
  pyproject.toml
  README.md
  src/onestep_feishu_bitable/
    __init__.py
    connector.py
    resources.py
  tests/
    test_feishu_bitable_connector.py
```

`connector.py` 放运行时实现：connector、source、sink、helper、错误类型。

`resources.py` 只放 YAML 构建和 strict 校验 handler，并通过 `register_resources(registry)` 注册到 core registry。

`__init__.py` 导出插件公开 Python API，并提供 entry point 目标 `register = register_resources`。

## Entry Point

插件 `pyproject.toml` 注册：

```toml
[project.entry-points."onestep.resources"]
feishu_bitable = "onestep_feishu_bitable:register"
```

worker 环境只要安装了插件包，`load_app_config(...)` 就会在 strict 校验和资源构建前加载 entry point，YAML 可以直接使用 `type: feishu_bitable*`。

## 同仓库开发

root `pyproject.toml` 使用 uv workspace：

```toml
[tool.uv.workspace]
members = [
    "plugins/onestep-feishu-bitable",
]
```

本地验证插件时使用：

```bash
uv run --all-packages python -m pytest plugins/onestep-feishu-bitable/tests
```

`--all-packages` 会把 workspace member 安装进环境，确保测试覆盖真实 package metadata 和 entry point，而不是只依赖 `PYTHONPATH`。

## 对 onestep worker 的影响

已有 worker 如果没有使用 Feishu Bitable，不受影响。

使用 Feishu Bitable 的 worker 需要安装 `onestep-feishu-bitable` 插件，并把 Python helper 导入改为：

```python
from onestep_feishu_bitable import FeishuBitableConnector
from onestep_feishu_bitable import feishu_bitable_text, feishu_bitable_user
```

YAML resource type 仍然使用 `feishu_bitable`、`feishu_bitable_incremental`、`feishu_bitable_table_sink`。

## 测试

- core 测试确认 core import 不依赖插件。
- 插件测试覆盖 connector/source/sink 行为。
- 插件测试确认 `onestep.resources` entry point 存在。
- 插件 YAML 测试通过真实 entry point 自动注册资源类型。
