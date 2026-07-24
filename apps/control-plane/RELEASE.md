# 发版流程 - OneStep Control Plane

## 标准流程

当用户说"发版"时，执行以下步骤：

### 1. 检查并提交代码
```bash
cd /Users/miclon/development/onestep-control-plane
git status
git add .
git commit -m "..."
git push
```

### 2. 构建多平台 Docker 镜像
```bash
# 使用日期版本（格式：YYYY.MM.DD）
IMAGE_REPOSITORY=registry.cn-shanghai.aliyuncs.com/ceeg \
IMAGE_TAG=$(date +%Y.%m.%d) \
make docker-build-multi-arch

# 同时构建 latest 标签
IMAGE_REPOSITORY=registry.cn-shanghai.aliyuncs.com/ceeg \
IMAGE_TAG=latest \
make docker-build-multi-arch
```

### 3. 完成后汇报
汇报内容包括：
- Git commit 信息
- 构建的镜像列表（包含版本号和平台）
- 推送状态

## 重要配置

### 镜像仓库
- **Registry**: `registry.cn-shanghai.aliyuncs.com/ceeg`
- **项目**: `onestep-control-plane`

### 镜像命名
- **Plane**: `onestep-control-plane`
- 单个镜像同时包含 API、WebSocket 服务和构建后的前端静态资源。

### 版本标签
1. **日期版本**: `YYYY.MM.DD`（如 `2026.03.11`）- 生产环境使用
2. **最新版本**: `latest` - 开发/测试环境使用

### 平台支持
- **linux/amd64** - x86_64 架构（大多数服务器）
- **linux/arm64** - ARM64 架构（Mac Apple Silicon、树莓派等）

## Makefile 命令

```bash
# 单平台构建（当前平台，不推荐）
make docker-build

# 多平台构建（推荐，自动推送）
IMAGE_REPOSITORY=xxx make docker-build-multi-arch

# 只构建统一 plane 镜像多平台
IMAGE_REPOSITORY=xxx make docker-build-plane-multi-arch

# 查看将要构建的镜像名称
IMAGE_REPOSITORY=xxx make docker-print-images
```

多平台构建默认会按当前 Docker context 使用独立 builder，例如在 `orbstack` 下默认使用 `multiarch-builder-orbstack`，并在缺失时自动创建一个 `docker-container` 类型的 buildx builder。
如果当前 shell 设置了 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 或 `NO_PROXY`，这些代理配置也会自动传给 buildx builder 和构建过程。
如果需要指定其他 builder，可覆盖 `BUILDX_BUILDER`：

```bash
IMAGE_REPOSITORY=xxx BUILDX_BUILDER=my-builder make docker-build-multi-arch
```

## 注意事项

1. **必须构建多平台** - 使用 `docker-build-multi-arch`，不是 `docker-build`
2. **日期版本** - 格式为 `YYYY.MM.DD`，使用当前日期
3. **两个标签** - 日期版本 + latest 标签都需要推送
4. **Git 提交** - 确保所有修改都已提交并推送
5. **镜像仓库** - 阿里云 registry.cn-shanghai.aliyuncs.com/ceeg
6. **统一镜像** - Dockerfile 的发布 stage 是 `plane`，不要再使用旧的 `api` / `frontend` stage。

## 快捷命令

```bash
# 完整发版流程
cd /Users/miclon/development/onestep-control-plane
git add . && git commit -m "..." && git push
IMAGE_REPOSITORY=registry.cn-shanghai.aliyuncs.com/ceeg IMAGE_TAG=$(date +%Y.%m.%d) make docker-build-multi-arch
IMAGE_REPOSITORY=registry.cn-shanghai.aliyuncs.com/ceeg IMAGE_TAG=latest make docker-build-multi-arch
```
