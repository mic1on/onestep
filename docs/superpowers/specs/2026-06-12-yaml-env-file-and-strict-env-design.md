# YAML: .env 文件与 strict_env 环境变量检查 设计

## 摘要

扩展 YAML 应用配置的环境变量能力:在已有 `${VAR}` / `${VAR:-default}` 字符串插值的基础上,增加两项互补能力:

1. **`.env` 文件自动加载** — YAML 加载阶段从同目录 `.env` 或 `app.env_file` 指定的路径读取 key=value 条目,注入 `os.environ`(不覆盖已有变量)。
2. **`strict_env` 环境变量存在性检查** — 扫描整棵 YAML 配置树中的 `${VAR}` 引用,对那些**没有默认值**且**不在环境中**的变量,在启动阶段就报错。

这两项能力都是**纯粹的配置加载期增强**,不改变 runtime、Source/Sink、control-plane 协议。

## 背景

当前 `src/onestep/config.py` 中的 `_expand_env_var` + `_expand_env_vars` 已经支持字符串级别的环境变量替换。但实际部署中会遇到两类高频问题:

1. **环境变量管理分散** — 每个任务需要的变量必须在 shell 里 `export` 或通过 systemd `EnvironmentFile=` 注入,没有一个与 YAML 文件配对的声明式方案。开发者常常在 YAML 旁边手写 `.env` 然后自己写 wrapper 脚本来加载。
2. **漏设变量不易发现** — 如果某个 `${DB_PASSWORD}` 被漏设,当前行为是展开为空字符串,只有在真正连接数据库时才报错,定位成本高(尤其是在有多个长生命周期的 connector 时)。

这两项都属于 12-Factor 应用的标准能力(dotenv + config validation),但 onestep 目前需要依赖外部工具来实现。本设计把它们纳入 core,保持零新增依赖。

## 目标

- YAML 应用可以从 `.env` 文件读取环境变量,默认路径为 YAML 同目录,也可显式指定 `app.env_file`。
- `app.strict_env: true` 或 CLI `--strict-env` 启用存在性检查,缺失时在 `onestep check` / `onestep run` 启动阶段就报错并列出所有缺失变量。
- 已有默认值的 `${VAR:-default}` 和 `${VAR:default}` 视为满足存在性要求,不触发 strict_env 错误。
- 不引入 python-dotenv 等外部包;使用标准库实现,覆盖常见格式。
- 已有无 env_file 配置的 YAML 保持行为完全不变。
- 已有在 shell 中设置的环境变量优先级高于 `.env` 文件(即 setdefault 语义)。

## 非目标

- 不支持 `.env` 中的嵌套变量引用(如 `BAR=${FOO}/bar`),一期只做简单 `KEY=VALUE` 解析。
- 不支持 `$VAR` 不带大括号的 shell 风格引用,避免误伤任意包含 `$` 的字符串(如 URL token)。
- 不做环境变量的类型转换(字符串 → int/float/bool);数值仍由各 connector builder 自己解析。
- 不做多层级 `.env` 合并(如 `.env.local` + `.env`);一期只加载一个文件。
- 不做运行时环境变量热更新;`.env` 仅在应用启动期读取一次。

## 用户可见行为

### 最小例子:自动加载同目录 `.env`

项目布局:
```
worker.yaml
.env
```

`.env` 内容:
```
# 数据库连接
DB_DSN=mysql+pymysql://root:secret@localhost:3306/app
DB_POOL_SIZE=20

# 飞书
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=my_secret
```

`worker.yaml`:
```yaml
app:
  name: billing-sync

resources:
  db:
    type: mysql
    dsn: "${DB_DSN}"
    pool_size: "${DB_POOL_SIZE}"

  feishu:
    type: feishu_bitable
    app_id: "${FEISHU_APP_ID}"
    app_secret: "${FEISHU_APP_SECRET}"

tasks:
  - name: sync_orders
    source: orders_source
    emit: orders_sink
```

运行时自动读取 `.env`,变量注入环境,随后正常展开 YAML:

```bash
onestep check worker.yaml   # OK
onestep run worker.yaml      # OK
```

### 显式指定 env 文件

```yaml
app:
  name: billing-sync
  env_file: .env.production     # 相对 YAML 路径;也可以是绝对路径
```

CLI 覆盖(优先级最高):
```bash
onestep run --env-file /etc/onestep/prod.env worker.yaml
```

### strict_env:启动即发现漏设变量

```yaml
app:
  name: billing-sync
  strict_env: true
```

如果 `DB_PASSWORD` 漏设,`onestep check` 报错:
```
ValueError: strict_env: missing required environment variable(s):
  - DB_PASSWORD    (referenced at: resources.db.dsn)
  - API_TOKEN      (referenced at: resources.http_api.token)
Hint: define them in the shell, or add them to your .env file,
      or provide a default with ${VAR:-default_value}.
```

CLI 覆盖:
```bash
onestep check --strict-env worker.yaml
onestep run --strict-env worker.yaml
```

### 混合:有默认值的变量不算缺失

```yaml
resources:
  http_api:
    timeout: "${API_TIMEOUT:-30}"   # 有默认值,即使未设也不报错
    token: "${API_TOKEN}"           # 无默认值,strict_env 下会检查
```

## 设计细节

### 1. 加载流程(在 `load_yaml_app` 内展开)

```
load_yaml_app(path, strict=False, env_file=None, strict_env=None)
  │
  ├── 解析 YAML → raw_config(尚未展开 env 变量)
  │
  ├── 决定 env_file 路径(优先级从高到低):
  │     a) CLI --env-file 参数 → 直接使用
  │     b) raw_config["app"]["env_file"] → 用已有 os.environ 做一次
  │        _expand_env_var 展开(支持 `.env.${ENVIRONMENT}`)
  │     c) YAML 同目录的 ".env" → 自动尝试
  │     (如果是 c) 且文件不存在 → 静默跳过;如果是 a)/b) 且文件
  │      不存在 → ValueError)
  │
  ├── _load_dotenv(env_path)
  │     逐行解析 KEY=VALUE
  │     使用 os.environ.setdefault(key, value) 注入
  │     记录加载的 key 数量用于日志
  │
  ├── 决定 strict_env_flag:
  │     CLI --strict-env > raw_config["app"]["strict_env"] > False
  │
  ├── (如果 strict_env_flag 为 true)
  │     _collect_env_refs(raw_config)
  │       ├── 递归遍历 str / list / dict
  │       ├── 对每个字符串,正则匹配所有 ${VAR} 模式
  │       ├── 对每个匹配:
  │       │     - 发现 ${VAR:-default} 或 ${VAR:default} → 跳过
  │       │     - 发现 ${VAR} 且 VAR not in os.environ → 记录为缺失
  │       └── 返回 {missing_var: [field_path, ...]}
  │     └── 有缺失 → 抛 ValueError(见"错误处理"章节)
  │
  ├── _expand_env_vars(raw_config)  ← 现有逻辑,不变
  │
  └── load_app_config(expanded, ...)  ← 现有逻辑,不变
```

### 2. `_load_dotenv` 解析器实现

纯标准库实现(读取文本文件 + 逐行解析):

```python
_LINE_RE = re.compile(
    r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*'   # KEY=
    r'(?:"([^"]*)"|\'([^\']*)\'|([^\s#][^#]*?))'   # "value" / 'value' / value
    r'\s*(?:\#.*)?$'                           # 可选行尾注释
)
# value 部分有 4 个捕获组(双引号/单引号/裸值),取第一个非空值

# 解析规则:
# - 空行 / 以 # 开头的整行注释 → 跳过
# - 匹配正则 → (key, value),剥离引号后 setdefault
# - 其他行(无法匹配) → 警告日志,不报错(保持对手写文件的宽容)
# - setdefault 语义:同一个 key 在 shell 中已设 → 跳过;文件中
#   出现多次同一个 key → 第一次出现的值 wins(后续 setdefault 跳过)
# - 不处理 \n、\t 等转义序列;value 就是原始字符串(和 YAML
#   safe_load 对普通字符串的行为一致)
```

### 3. `_collect_env_refs` 实现

```python
_REF_RE = re.compile(r"\$\{([^}]+)\}")

def _collect_env_refs(value, field_path, missing):
    if isinstance(value, str):
        for match in _REF_RE.finditer(value):
            content = match.group(1)
            # 检查是否有默认值
            if ":-" in content or ":" in content:
                continue
            var_name = content.strip()
            if var_name and var_name not in os.environ:
                missing.setdefault(var_name, []).append(field_path)
    elif isinstance(value, Mapping):
        for k, v in value.items():
            _collect_env_refs(v, f"{field_path}.{k}", missing)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _collect_env_refs(v, f"{field_path}[{i}]", missing)
```

### 4. strict 模式的语义组合

- `onestep check --strict`:启用字段检查 + connector type 检查;**是否同时启用 strict_env** 由 `app.strict_env` 字段决定。这样 `--strict` 不会突然让已有项目因缺少 env 变量而报错。
- `onestep check --strict-env`:单独启用环境变量检查,不影响其他严格模式规则。可以与 `--strict` 叠加。
- `onestep run --strict-env`:运行时启动前检查(和 check 一致)。

### 5. YAML schema 变更

`_STRICT_APP_FIELDS` 扩展:
```python
_STRICT_APP_FIELDS = frozenset({
    "name", "shutdown_timeout_s", "config", "state", "logging",
    "env_file", "strict_env",   # 新增
})
```

`validate_app_config` 中新增对 `app.env_file` 类型校验(必须是字符串,如果提供)和 `app.strict_env` 类型校验(必须是 bool)。

### 6. CLI 参数变更(`src/onestep/cli.py`)

在 `run` / `check` 子命令中新增:
- `--env-file PATH`:str,优先级覆盖 YAML 中的 `app.env_file`
- `--strict-env`:flag,启用环境变量存在性检查(覆盖 YAML 中的 `app.strict_env`)

参数传入 `load_yaml_app`,由其决定最终使用值。

## 错误处理

| 场景 | 结果 | 错误消息设计 |
|------|------|-------------|
| `env_file` 显式指定但路径不存在 | ValueError | `env_file not found: /path/to/file` |
| `env_file` 显式指定但不是文件 | ValueError | `env_file is not a regular file: /path/to/dir` |
| `strict_env` 且有变量缺失 | ValueError | 列出所有缺失变量 + 引用位置,附 Hint |
| `.env` 中一行无法解析 | 警告日志 + 跳过该行 | `skipped malformed .env line 42: 'bad line here'` |
| 自动查找的 `.env` 不存在 | 静默跳过 | `DEBUG: no .env found next to worker.yaml` |
| `.env` 中 key 已在 shell 中设置 | 使用 shell 值,`.env` 中的值被忽略 | `DEBUG: skipped DB_DSN from .env (already set in environment)` |
| `strict_env` 下所有引用都有默认值或已设置 | 正常通过 | 无额外日志 |

## 测试策略

所有测试落在 `tests/test_config.py`(扩展现有文件或新增文件),不影响插件的测试。

### 单元测试

1. **`_load_dotenv` 基本功能**
   - 解析 `KEY=VALUE`、`KEY="VALUE"`、`KEY='VALUE'`、`# 注释`、空行
   - shell 中已有变量不会被 `.env` 覆盖
   - 显式指定的 `env_file` 不存在 → ValueError

2. **`_collect_env_refs` 收集与判断**
   - `${VAR}` 在环境中 → 不记录
   - `${VAR}` 不在环境中 → 记录为缺失
   - `${VAR:-default}` 无论是否在环境中 → 不记录
   - 递归进入 list/dict,`field_path` 正确指向 `resources.db.dsn` 形式
   - 非字符串值(int、bool、None)被跳过

3. **`_expand_env_var` 回归测试**(确保不破坏现有 `${VAR}` / `${VAR:-default}` 行为)

4. **strict_env 端到端**
   - 临时文件 + 临时 os.environ patch
   - `load_yaml_app` 在缺失变量时报错,错误消息包含变量名和引用位置
   - 设置变量后再次加载,通过

5. **env_file 优先级**
   - CLI `--env-file` > YAML `app.env_file` > 自动同目录 `.env`

6. **env_file 自动查找静默跳过**
   - 无 `.env` 文件的情况下,加载不受影响
   - 有 `.env` 但内容为空,也不报错

7. **CLI 参数透传**
   - `onestep check --env-file --strict-env` 参数传递到 `load_yaml_app`

### 集成测试

- 使用 `example/yaml_project/` 作为 base,添加临时 `.env`,确保 `onestep check` 成功
- 添加一个引用不存在变量的 YAML,配合 `--strict-env`,断言返回非零退出码和可读错误消息

## 实现路径(修改的文件)

```
src/onestep/config.py
  ├── 新增: _DOTENV_LINE_RE, _ENV_REF_RE(模块级常量)
  ├── 新增: _load_dotenv(path) -> dict(loaded_keys)
  ├── 新增: _collect_env_refs(config) -> dict(var_name -> [field_path])
  ├── 修改: load_yaml_app 增加 env_file / strict_env 参数,按流程插入
  ├── 修改: _STRICT_APP_FIELDS 添加 env_file, strict_env
  ├── 修改: validate_app_config 中 app section 的类型校验扩展
  └── 保持: _expand_env_var / _expand_env_vars / _expand_env_vars_in_string 不变

src/onestep/cli.py
  └── run / check 子命令新增 --env-file / --strict-env argparse 参数

tests/test_config.py (或新增 tests/test_config_env.py)
  └── 7+ 个单元测试 + 端到端测试
```

预估代码改动约 180~250 行新增,零破坏性变更,不改变现有公共 API 签名。
