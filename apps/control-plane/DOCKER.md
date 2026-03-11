# Docker 镜像构建和发布指南

## 镜像版本标签规范

本项目的 Docker 镜像使用双重版本标签：

### 1. 当前版本标签
- **格式**：`YYYY.MM.DD`（年.月.日）
- **示例**：`2026.03.11`
- **用途**：标识具体发布版本，便于追溯和回滚

### 2. 最新版本标签
- **格式**：`latest`
- **用途**：始终指向最新的稳定版本，用于开发和测试环境

## 完整镜像列表

| 镜像 | 标签 | 说明 |
|------|------|------|
| API | `registry.cn-shanghai.aliyuncs.com/ceeg/onestep-control-plane-api:YYYY.MM.DD` | 具体版本 |
| API | `registry.cn-shanghai.aliyuncs.com/ceeg/onestep-control-plane-api:latest` | 最新版本 |
| Frontend | `registry.cn-shanghai.aliyuncs.com/ceeg/onestep-control-plane-frontend:YYYY.MM.DD` | 具体版本 |
| Frontend | `registry.cn-shanghai.aliyuncs.com/ceeg/onestep-control-plane-frontend:latest` | 最新版本 |

## 构建和发布流程

### 1. 构建镜像

#### 单架构构建（本地平台）
```bash
# 构建所有镜像（使用默认的 commit hash 作为 tag）
make docker-build

# 构建单个镜像
make docker-build-api
make docker-build-frontend
```

#### 多架构构建（amd64 + arm64）
```bash
# 构建并推送多架构镜像（会直接推送到 registry）
IMAGE_REPOSITORY=registry.cn-shanghai.aliyuncs.com/ceeg make docker-build-multi-arch

# 构建单个多架构镜像
IMAGE_REPOSITORY=registry.cn-shanghai.aliyuncs.com/ceeg make docker-build-api-multi-arch
IMAGE_REPOSITORY=registry.cn-shanghai.aliyuncs.com/ceeg make docker-build-frontend-multi-arch
```

**注意**：
- 多架构构建会自动推送镜像，无法创建本地镜像
- 多架构构建耗时较长（需要同时编译 amd64 和 arm64）
- 多架构构建使用 `docker buildx`，需要 QEMU 模拟器支持

### 2. 打标签

```bash
# 使用日期格式打标签
export VERSION=$(date +%Y.%m.%d)
docker tag onestep-control-plane-api:$(git rev-parse --short HEAD) \
  registry.cn-shanghai.aliyuncs.com/ceeg/onestep-control-plane-api:$VERSION
docker tag onestep-control-plane-frontend:$(git rev-parse --short HEAD) \
  registry.cn-shanghai.aliyuncs.com/ceeg/onestep-control-plane-frontend:$VERSION

# 打 latest 标签
docker tag registry.cn-shanghai.aliyuncs.com/ceeg/onestep-control-plane-api:$VERSION \
  registry.cn-shanghai.aliyuncs.com/ceeg/onestep-control-plane-api:latest
docker tag registry.cn-shanghai.aliyuncs.com/ceeg/onestep-control-plane-frontend:$VERSION \
  registry.cn-shanghai.aliyuncs.com/ceeg/onestep-control-plane-frontend:latest
```

### 3. 推送镜像

```bash
# 推送所有标签
make docker-push

# 或手动推送
docker push registry.cn-shanghai.aliyuncs.com/ceeg/onestep-control-plane-api:$VERSION
docker push registry.cn-shanghai.aliyuncs.com/ceeg/onestep-control-plane-api:latest
docker push registry.cn-shanghai.aliyuncs.com/ceeg/onestep-control-plane-frontend:$VERSION
docker push registry.cn-shanghai.aliyuncs.com/ceeg/onestep-control-plane-frontend:latest
```

## Makefile 使用

```bash
# 查看将要构建和推送的镜像名称
IMAGE_REPOSITORY=registry.cn-shanghai.aliyuncs.com/ceeg make docker-print-images

# 构建镜像（使用指定的镜像仓库前缀，单架构）
IMAGE_REPOSITORY=registry.cn-shanghai.aliyuncs.com/ceeg make docker-build

# 构建并推送多架构镜像（amd64 + arm64，生产推荐）
IMAGE_REPOSITORY=registry.cn-shanghai.aliyuncs.com/ceeg make docker-build-multi-arch

# 推送镜像（单架构镜像）
IMAGE_REPOSITORY=registry.cn-shanghai.aliyuncs.com/ceeg make docker-push
```

## 使用镜像

### 生产环境（推荐使用日期版本）
```bash
docker pull registry.cn-shanghai.aliyuncs.com/ceeg/onestep-control-plane-api:2026.03.11
docker pull registry.cn-shanghai.aliyuncs.com/ceeg/onestep-control-plane-frontend:2026.03.11
```

### 开发/测试环境（使用 latest）
```bash
docker pull registry.cn-shanghai.aliyuncs.com/ceeg/onestep-control-plane-api:latest
docker pull registry.cn-shanghai.aliyuncs.com/ceeg/onestep-control-plane-frontend:latest
```

## 镜像架构

本项目支持多架构构建，同时支持 amd64 和 arm64 平台。

### 支持的平台

| 平台 | 架构 | 典型使用场景 |
|------|------|--------------|
| `linux/amd64` | x86_64 | 大多数服务器、云主机、Windows WSL2 |
| `linux/arm64` | ARM64 | Mac (Apple Silicon)、树莓派、ARM 服务器 |

### 构建方式选择

**单架构构建**（默认）：
- 构建速度快
- 适合本地开发和测试
- 只生成当前平台的镜像
- 命令：`make docker-build`

**多架构构建**（生产推荐）：
- 一次构建，同时支持多种平台
- 适合生产环境部署
- 构建时间较长（需等待两个平台编译完成）
- 会自动推送到 registry，无法创建本地镜像
- 命令：`IMAGE_REPOSITORY=xxx make docker-build-multi-arch`

### 镜像内容

- **API 镜像**：基于 `ghcr.io/astral-sh/uv:python3.11-bookworm-slim`
  - 包含 Python 3.11 运行时
  - 使用 uv 包管理器
  - 包含 Alembic 数据库迁移工具
  - 暴露端口：8000
  - 支持平台：amd64, arm64

- **Frontend 镜像**：基于 `nginx:1.27-alpine`
  - 前端构建产物通过 Vite 生成
  - Nginx 作为静态文件服务器
  - 暴露端口：80
  - 支持平台：amd64, arm64

## 注意事项

1. **每日构建**：同一日期的多次构建会覆盖之前的镜像（标签相同）
2. **版本追溯**：重要版本应记录 Git commit hash，便于追溯
3. **环境隔离**：生产环境使用日期版本，开发环境可使用 latest
4. **镜像大小**：API 镜像约 328MB，Frontend 镜像约 50MB
