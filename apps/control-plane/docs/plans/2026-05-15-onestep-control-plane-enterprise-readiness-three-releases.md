# OneStep Control Plane Enterprise Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 3 个发版周期内，把 `onestep-control-plane` 从当前的 alpha 控制台提升到企业内部生产可用的控制面，不引入 `SSO`，但具备本地多用户认证、`RBAC`、真实 `readiness`、可观测、可审计、可恢复、可压测的运行基线。

**Architecture:** 继续把 `onestep` runtime 视为执行引擎，把 `onestep-control-plane` 视为运维控制面。发版顺序采用 `Release 1 -> Release 2 -> Release 3`，分别解决生产准入、稳定运营、企业增强。每次发版按 `backend / frontend / infra / docs` 四条线拆包，并为多智能体协同预留明确的文件所有权，尽量避免同一发版内多个智能体同时改同一文件。

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, PostgreSQL, React, TypeScript, TanStack Query, Vite, Docker Compose, GitHub Actions, Prometheus/OpenTelemetry-compatible metrics.

---

## Scope And Assumptions

- 不做 `SSO`，改做本地账号体系。
- 目标是企业内部生产可用，不做多租户 SaaS。
- 数据库以 PostgreSQL 为生产标准，SQLite 只保留测试/本地开发兜底。
- `onestep` runtime 协议保持兼容，不把本计划扩展成 runtime 重写项目。
- 本计划优先补齐控制面的工程化与运维能力，不追求新增大量 UI 功能。

## Parallel Development Rules

- 每个发版指定 1 个 `API contract owner`，独占以下高冲突文件：
  - `backend/src/onestep_control_plane_api/api/schemas.py`
  - `frontend/src/lib/api/types.ts`
  - `frontend/src/lib/api/client.ts`
- 每个发版指定 1 个 `migration owner`，独占以下高冲突文件：
  - `backend/src/onestep_control_plane_api/db/models.py`
  - `backend/alembic/versions/*`
- 每个发版指定 1 个 `release owner`，独占以下高冲突文件：
  - `Dockerfile`
  - `docker-compose.deploy.yml`
  - `.github/workflows/*`
- 多智能体并行时，任何任务都不得主动修改未归属给自己的文件。
- 每个发版都先冻结 API contract，再启动前后端并行开发。
- 每个发版只允许 1 个数据库迁移 PR 合入主线，避免 Alembic head 冲突。

## Shared Hotspots

- `backend/src/onestep_control_plane_api/main.py`
- `backend/src/onestep_control_plane_api/core/settings.py`
- `backend/src/onestep_control_plane_api/api/security.py`
- `frontend/src/app/router.tsx`
- `frontend/src/pages/login/LoginPage.tsx`

这些文件在同一发版内只能有一个任务包持有修改权。其他任务若依赖它们，先通过 contract 或新增模块解耦。

## Release Map

| Release | Primary Outcome | Recommended Duration | Go/No-Go Gate |
| --- | --- | --- | --- |
| Release 1 | 生产准入 | 2-3 周 | 可安全上线，发布失败可阻断 |
| Release 2 | 稳定运营 | 2-3 周 | 可审计、可恢复、可控清理 |
| Release 3 | 企业增强 | 2-4 周 | 有容量边界、安全基线、演练记录 |

## Release 1: Production Admission

**Release goal:** 去掉共享账号模式，建立本地多用户认证和 `RBAC`，把 `readiness`、发布、CI、最小观测补齐，达到“可以谨慎生产上线”的门槛。

**Suggested owners:**

- Agent R1-A: Backend Auth / RBAC
- Agent R1-B: Backend Readiness / Lifecycle
- Agent R1-C: Frontend Auth / Route Guard / Tests
- Agent R1-D: Infra / CI / Release Safety / Docs

### Task R1-A: Local Auth And RBAC Foundation

**Files:**
- Create: `backend/src/onestep_control_plane_api/auth/__init__.py`
- Create: `backend/src/onestep_control_plane_api/auth/passwords.py`
- Create: `backend/src/onestep_control_plane_api/auth/service.py`
- Create: `backend/src/onestep_control_plane_api/auth/policies.py`
- Create: `backend/tests/test_local_auth.py`
- Create: `backend/tests/test_command_rbac.py`
- Create: `scripts/create_local_admin.py`
- Modify: `backend/src/onestep_control_plane_api/api/security.py`
- Modify: `backend/src/onestep_control_plane_api/api/routers/auth.py`
- Modify: `backend/src/onestep_control_plane_api/api/routers/query.py`
- Modify: `backend/src/onestep_control_plane_api/api/routers/commands.py`
- Modify: `backend/src/onestep_control_plane_api/api/schemas.py`
- Modify: `backend/src/onestep_control_plane_api/core/settings.py`
- Modify: `backend/src/onestep_control_plane_api/db/models.py`
- Modify: `pyproject.toml`
- Create: `backend/alembic/versions/202605150001_add_local_auth_and_rbac.py`

- [ ] 冻结本发版认证 contract，定义 `viewer / operator / admin` 三档角色以及命令权限矩阵。
- [ ] 新增本地账号表、角色表、用户角色关联表、会话表；不在 `Release 1` 引入 `MFA`。
- [ ] 引入密码哈希依赖并实现密码校验；禁止明文密码落库。
- [ ] 用数据库账号体系替换当前共享用户名密码登录模式；共享账号模式仅允许在 `dev` 中作为可选 bootstrap 兜底。
- [ ] 在 `query` 和 `commands` 路由上接入角色检查；`viewer` 只读，`operator` 只允许非破坏性命令，`admin` 才能执行 `shutdown / restart / drain`。
- [ ] 增加本地管理员初始化脚本，支持首次部署创建第一个管理员账户。
- [ ] 为登录、登出、会话失效、错误密码、角色校验、危险命令拒绝写后端测试。

**Acceptance Criteria:**

- 生产环境不再依赖共享账号环境变量完成控制台登录。
- `viewer` 无法提交控制命令，`operator` 无法提交破坏性命令，`admin` 可以执行全部命令。
- 密码以哈希形式存储，会话可以显式失效。
- 后端测试覆盖登录成功、登录失败、权限不足、会话过期四条主链路。

**Verify:**

- `uv run pytest backend/tests/test_console_auth.py backend/tests/test_local_auth.py backend/tests/test_command_rbac.py -q`
- `uv run ruff check backend/src/onestep_control_plane_api backend/tests scripts/create_local_admin.py`

### Task R1-B: Real Readiness And App Lifecycle

**Files:**
- Create: `backend/src/onestep_control_plane_api/ops/readiness.py`
- Modify: `backend/src/onestep_control_plane_api/main.py`
- Modify: `backend/src/onestep_control_plane_api/api/routers/health.py`
- Modify: `backend/src/onestep_control_plane_api/core/settings.py`
- Modify: `backend/src/onestep_control_plane_api/db/session.py`
- Modify: `backend/tests/test_health.py`
- Create: `backend/tests/test_readiness.py`

- [ ] 把 `/healthz` 定义为浅健康检查，把 `/readyz` 定义为深度就绪检查。
- [ ] 在 `readiness` 中检查数据库连接、当前迁移版本是否等于 `head`、后台扫描任务状态是否正常。
- [ ] 为 `main.py` 中的后台任务维护显式运行状态，避免接口始终盲目返回 `"ready"`。
- [ ] 为启动、关闭、后台扫描失败增加结构化日志字段，便于后续接入日志平台。
- [ ] 保证数据库不可达、迁移落后、后台任务已死时 `/readyz` 返回 `503`。
- [ ] 为浅检查和深检查分别补齐单元测试和集成测试。

**Acceptance Criteria:**

- `/readyz` 不再是固定成功响应。
- 发布流程可以基于 `/readyz` 做阻断。
- 启动失败原因可从日志中直接定位到数据库、迁移或后台任务状态。

**Verify:**

- `uv run pytest backend/tests/test_health.py backend/tests/test_readiness.py -q`
- `uv run python -c "from onestep_control_plane_api.main import create_app; create_app()"`

### Task R1-C: Frontend Auth Flow, Role Awareness, And Test Baseline

**Files:**
- Create: `frontend/vitest.config.ts`
- Create: `frontend/playwright.config.ts`
- Create: `frontend/src/test/setup.ts`
- Create: `frontend/src/pages/login/LoginPage.test.tsx`
- Create: `frontend/src/features/auth/RequireConsoleAuth.test.tsx`
- Create: `frontend/e2e/auth.spec.ts`
- Modify: `frontend/package.json`
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/features/auth/RequireConsoleAuth.tsx`
- Modify: `frontend/src/features/auth/queries.ts`
- Modify: `frontend/src/lib/api/client.ts`
- Modify: `frontend/src/lib/api/types.ts`
- Modify: `frontend/src/pages/login/LoginPage.tsx`
- Modify: `frontend/src/pages/service-detail/ServiceDetailPage.tsx`
- Modify: `frontend/src/pages/task-detail/TaskDetailPage.tsx`
- Modify: `frontend/src/pages/instance-detail/InstanceDetailPage.tsx`

- [ ] 接入新的本地账号登录接口和会话模型，处理登录、登出、会话过期、无权限四种状态。
- [ ] 根据后端角色模型控制前端按钮显示；`viewer` 不展示控制按钮，`operator` 不展示破坏性操作入口。
- [ ] 为登录页、路由守卫、会话失效跳转增加前端单测。
- [ ] 增加最小端到端测试，覆盖登录成功、未登录重定向、低权限账号隐藏操作按钮。
- [ ] 保持现有页面信息架构不变，不在本发版重构导航。

**Acceptance Criteria:**

- 未登录用户访问业务页会被正确跳转到登录页。
- 会话过期后会清空前端状态并重新要求登录。
- 低权限用户不会在 UI 上看到不允许执行的入口。
- 新增 `test` 与 `e2e` 脚本，可纳入 CI。

**Verify:**

- `pnpm --dir frontend test --run`
- `pnpm --dir frontend build`
- `pnpm --dir frontend exec playwright test`

### Task R1-D: CI, Release Safety, Migration Flow, And Runbooks

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/release-smoke.yml`
- Create: `scripts/run-smoke.sh`
- Create: `scripts/release-preflight.sh`
- Create: `docs/runbooks/release.md`
- Create: `docs/runbooks/rollback.md`
- Modify: `Dockerfile`
- Modify: `Makefile`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.deploy.yml`
- Modify: `README.md`

- [ ] 为后端增加 `ruff + pytest`，为前端增加 `test + build`，为镜像增加最小构建校验。
- [ ] 把 `alembic upgrade head` 从 API 容器启动命令中拆出，改为显式发布步骤。
- [ ] 增加基于 `docker compose` 的 smoke 脚本，至少覆盖 `postgres + api + frontend` 启动和 `readyz` 检查。
- [ ] 为回滚写清楚镜像回退、迁移失败中止、环境变量回滚步骤。
- [ ] 把发版脚本、检查脚本、回滚文档纳入仓库，避免口头运维。

**Acceptance Criteria:**

- PR 合并前自动执行后端、前端、镜像校验。
- API 容器不再自动迁移数据库。
- 新环境部署有明确的 preflight、migrate、smoke、rollback 流程。

**Verify:**

- `docker build --target api -t onestep-control-plane-api:test .`
- `docker build --target frontend -t onestep-control-plane-frontend:test .`
- `bash scripts/run-smoke.sh`

### Release 1 Gate

- [ ] staging 环境完成真实登录、只读账号、运维账号、管理员账号四类手工验证。
- [ ] `/readyz` 在数据库关闭时返回 `503`。
- [ ] 迁移失败时发布流程中止，API 不自动带着错误 schema 启动。
- [ ] `backend`、`frontend`、smoke 测试全部进入 CI。

## Release 2: Stable Operations

**Release goal:** 补齐备份恢复、后台任务单实例化和历史数据清理，达到”可以长期运营”的门槛。

**Suggested owners:**

- Agent R2-B: Backend Worker Leadership
- Agent R2-C: Backend Retention / Cleanup
- Agent R2-D: Frontend Ops UX / Degraded States
- Agent R2-E: Infra Backup / Alerts / Runbooks

### Task R2-B: Background Jobs Single-Owner Execution

**Files:**
- Create: `backend/src/onestep_control_plane_api/workers/__init__.py`
- Create: `backend/src/onestep_control_plane_api/workers/leader.py`
- Create: `backend/src/onestep_control_plane_api/workers/notification_scanner.py`
- Create: `backend/tests/test_worker_leadership.py`
- Modify: `backend/src/onestep_control_plane_api/main.py`
- Modify: `backend/src/onestep_control_plane_api/api/notification_service.py`
- Modify: `backend/src/onestep_control_plane_api/core/settings.py`

- [ ] 把 missed-start 扫描逻辑从裸 `lifespan` 任务抽象成可管理 worker。
- [ ] 为多副本部署引入单实例执行策略，优先采用 PostgreSQL advisory lock 或显式 leader lease。
- [ ] worker 失去租约或异常退出时，必须释放资源并记录清晰日志。
- [ ] 为单副本、多副本、leader 切换三种场景补测试。

**Acceptance Criteria:**

- 多个 API 副本同时运行时，通知扫描只会有一个活跃执行者。
- 活跃 leader 退出后，后续副本能在可接受时间内接管。
- 就绪检查可识别本地 worker 状态与租约状态。

**Verify:**

- `uv run pytest backend/tests/test_notification_service.py backend/tests/test_worker_leadership.py -q`

### Task R2-C: Retention, Cleanup, And Historical Data Control

**Files:**
- Create: `backend/src/onestep_control_plane_api/ops/retention.py`
- Create: `backend/tests/test_retention.py`
- Create: `scripts/run-retention.py`
- Modify: `backend/src/onestep_control_plane_api/core/settings.py`
- Modify: `backend/src/onestep_control_plane_api/db/models.py`
- Create: `backend/alembic/versions/202605150003_add_retention_indexes.py`

- [ ] 为 `task_events`、`task_metric_windows`、`agent_commands` 定义默认 retention 策略和可配置阈值。
- [ ] 实现 dry-run 与 execute 两种清理模式，先出报告，再允许真实删除。
- [ ] 为大表补必要索引，保证清理和查询不会互相拖垮。
- [ ] 输出清理统计日志，供后续告警或 dashboard 使用。
- [ ] 为清理脚本补测试，覆盖无数据、小批量、过量数据三种情况。

**Acceptance Criteria:**

- 老数据有统一清理入口，不再无限增长。
- 清理任务支持先 dry-run 再 execute。
- 查询索引与清理路径都有测试和文档。

**Verify:**

- `uv run pytest backend/tests/test_retention.py -q`
- `uv run python scripts/run-retention.py --dry-run`

### Task R2-D: Frontend Degraded-State UX And Command Review Flow

**Files:**
- Create: `frontend/src/features/commands/components/DestructiveCommandReviewDialog.tsx`
- Create: `frontend/src/features/commands/components/ConnectionStatusBanner.tsx`
- Create: `frontend/src/pages/instance-detail/InstanceDetailPage.test.tsx`
- Create: `frontend/e2e/destructive-command.spec.ts`
- Modify: `frontend/src/features/commands/queries.ts`
- Modify: `frontend/src/features/commands/useCommandStream.ts`
- Modify: `frontend/src/pages/service-detail/ServiceDetailPage.tsx`
- Modify: `frontend/src/pages/task-detail/TaskDetailPage.tsx`
- Modify: `frontend/src/pages/instance-detail/InstanceDetailPage.tsx`
- Modify: `frontend/src/components/ui/ToastProvider.tsx`
- Modify: `frontend/src/lib/api/types.ts`

- [ ] 为破坏性命令增加二次确认 UI，显示目标范围、原因输入、危险等级。
- [ ] 在 WS 断连、数据陈旧、命令失败、API 超时场景下增加统一降级提示。
- [ ] 为破坏性命令流程与断连状态分别增加单测和 e2e。

**Acceptance Criteria:**

- 用户不会在缺少确认的情况下误触发破坏性命令。
- 页面在控制面断连或数据陈旧时有明确告警，不出现无提示空白状态。
- 命令历史中能看到更完整的原因和确认信息。

**Verify:**

- `pnpm --dir frontend test --run`
- `pnpm --dir frontend exec playwright test frontend/e2e/destructive-command.spec.ts`

### Task R2-E: Backup, Restore, Alerts, And Runbooks

**Files:**
- Create: `scripts/backup-postgres.sh`
- Create: `scripts/restore-postgres.sh`
- Create: `monitoring/prometheus/rules/control-plane.yml`
- Create: `docs/runbooks/backup-restore.md`
- Create: `docs/runbooks/alerts.md`
- Modify: `README.md`

- [ ] 固化 PostgreSQL 备份脚本和恢复脚本，要求能在 staging 环境完成一次恢复演练。
- [ ] 为数据库不可用、WS 连接异常、命令失败率异常、通知投递失败定义告警规则。
- [ ] 把“如何恢复、如何判断恢复成功、何时升级告警”写进 runbook。
- [ ] 为恢复演练记录输入、输出、恢复时长和遗留问题。

**Acceptance Criteria:**

- 备份和恢复流程可重复执行，有明确文档和脚本。
- 至少一轮恢复演练完成并形成记录。
- 关键告警项有统一定义，不依赖人工肉眼盯盘。

**Verify:**

- `bash scripts/backup-postgres.sh`
- `bash scripts/restore-postgres.sh`

### Release 2 Gate

- [ ] staging 或预生产完成一次真实备份恢复演练。
- [ ] 多副本 API 下后台扫描没有重复执行。
- [ ] retention 具备 dry-run 输出和真实执行能力。

## Release 3: Enterprise Hardening

**Release goal:** 在已有生产基线之上，补齐 `MFA`、更细粒度权限、容量基线、供应链安全和故障演练，达到“可规模化推广”的门槛。

**Suggested owners:**

- Agent R3-A: Backend MFA / Session Hardening
- Agent R3-B: Scoped RBAC / Environment Guardrails
- Agent R3-C: Capacity / Load / SLO
- Agent R3-D: Supply Chain Security / Hardening Docs

### Task R3-A: MFA And Session Hardening

**Files:**
- Create: `backend/src/onestep_control_plane_api/auth/mfa.py`
- Create: `backend/tests/test_mfa.py`
- Modify: `backend/src/onestep_control_plane_api/api/security.py`
- Modify: `backend/src/onestep_control_plane_api/api/routers/auth.py`
- Modify: `backend/src/onestep_control_plane_api/api/schemas.py`
- Modify: `backend/src/onestep_control_plane_api/db/models.py`
- Modify: `frontend/src/pages/login/LoginPage.tsx`
- Modify: `frontend/src/lib/api/types.ts`
- Create: `backend/alembic/versions/202605150004_add_mfa_fields.py`

- [ ] 为本地账号体系增加可选 `TOTP MFA`，默认对 `admin` 启用。
- [ ] 增加会话轮换、显式登出全部会话、敏感操作前再次校验的能力。
- [ ] 在登录 UI 中支持二段式验证码输入。
- [ ] 为无 `MFA`、错误 `MFA`、会话轮换补测试。

**Acceptance Criteria:**

- 管理员账号支持开启并验证 `MFA`。
- 敏感操作可以要求近期重新认证或二次验证。
- 会话安全能力不会破坏普通账号登录流。

**Verify:**

- `uv run pytest backend/tests/test_mfa.py backend/tests/test_local_auth.py -q`
- `pnpm --dir frontend test --run`

### Task R3-B: Scoped RBAC And Environment-Level Guardrails

**Files:**
- Create: `backend/tests/test_scoped_rbac.py`
- Modify: `backend/src/onestep_control_plane_api/auth/policies.py`
- Modify: `backend/src/onestep_control_plane_api/api/security.py`
- Modify: `backend/src/onestep_control_plane_api/api/routers/query.py`
- Modify: `backend/src/onestep_control_plane_api/api/routers/commands.py`
- Modify: `backend/src/onestep_control_plane_api/api/schemas.py`
- Modify: `backend/src/onestep_control_plane_api/db/models.py`
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/lib/routes.ts`
- Modify: `frontend/src/pages/services-list/ServicesListPage.tsx`
- Create: `backend/alembic/versions/202605150005_add_role_scopes.py`

- [ ] 在现有 `RBAC` 上增加环境或服务级 scope，不允许跨环境误操作。
- [ ] 为 `prod` 环境下的破坏性命令增加更严格的策略开关。
- [ ] 在服务列表和详情页中根据 scope 过滤可见资源。
- [ ] 为 scope 生效、scope 越权、prod 限制补测试。

**Acceptance Criteria:**

- 用户权限不再只停留在角色级别，能约束到环境或服务范围。
- 即使是 `operator` 或 `admin`，也不能越权操作未授权环境。
- 前后端显示与后端权限策略一致。

**Verify:**

- `uv run pytest backend/tests/test_scoped_rbac.py -q`
- `pnpm --dir frontend test --run`

### Task R3-C: Capacity Baseline, Load Tests, And SLOs

**Files:**
- Create: `scripts/load/ws_agent_load.py`
- Create: `scripts/load/query_load.py`
- Create: `docs/reports/2026-xx-capacity-baseline.md`
- Create: `docs/runbooks/slo.md`
- Modify: `README.md`

- [ ] 为 WS 接入、事件写入、查询 API 分别准备基础压测脚本。
- [ ] 在 staging 环境得出连接数、吞吐、P95/P99 响应时间的基线数字。
- [ ] 定义控制面 `SLO`，至少包含 API 可用性、命令成功率、实例在线判定时效。
- [ ] 输出一份容量报告，明确当前部署建议和已知瓶颈。

**Acceptance Criteria:**

- 不再用口头经验描述容量，必须有数字和测试方法。
- `SLO` 有书面定义，且与告警策略关联。
- 压测脚本可复用，并能在未来回归使用。

**Verify:**

- `uv run python scripts/load/ws_agent_load.py --help`
- `uv run python scripts/load/query_load.py --help`

### Task R3-D: Supply Chain Security And Deployment Hardening

**Files:**
- Create: `.github/workflows/security.yml`
- Create: `.github/dependabot.yml`
- Create: `docs/runbooks/security-baseline.md`
- Modify: `Dockerfile`
- Modify: `Makefile`
- Modify: `README.md`

- [ ] 为 Python 依赖、Node 依赖、镜像构建增加漏洞扫描与升级提醒。
- [ ] 固化基础镜像版本，不继续使用隐式漂移标签。
- [ ] 检查容器运行用户、只读文件系统、最小权限挂载等部署硬化项。
- [ ] 把安全基线、升级频率、漏洞响应流程写成 runbook。

**Acceptance Criteria:**

- 仓库具备自动依赖提醒和镜像安全扫描。
- 生产镜像基线固定且可追踪。
- 安全基线不是口头约定，而是进入仓库文档。

**Verify:**

- `docker build --target api -t onestep-control-plane-api:security .`
- `docker build --target frontend -t onestep-control-plane-frontend:security .`

### Release 3 Gate

- [ ] `MFA` 和 scoped `RBAC` 在 staging 完成管理员与运维账号验证。
- [ ] 容量报告和 `SLO` 文档进入仓库。
- [ ] 依赖和镜像扫描进入 CI。
- [ ] 至少完成一次 WS 断连或数据库抖动的故障演练，并写入运行手册。

## Merge Order Per Release

1. 冻结 API contract 与 migration owner。
2. 合入 backend contract/migration PR。
3. 合入 frontend PR。
4. 合入 infra/docs PR。
5. 跑一次全量验证并切 release tag。

## Global Release Definition Of Done

- [ ] 对应 release 的测试、构建、smoke、runbook 全部合入主线。
- [ ] 对应 release 的 staging 手工验证完成并留痕。
- [ ] 下个 release 所需的 contract 变更已经从本 release 中收口，不把半成品拖到后续发版。
- [ ] 所有新增环境变量、脚本、运维步骤都有 README 或 runbook 记录。

## Recommended Multi-Agent Execution Model

- 每个发版先由 `release owner` 建立工作分解和文件所有权表。
- 后端、前端、infra 各自单独开分支或独立 worktree。
- 先落 `contract PR`，再跑并行实现。
- 每个任务包结束时都必须附带：
  - 修改文件列表
  - 新增/修改测试
  - 验证命令
  - 风险与未解决项
- 同一发版中，任何智能体都不得顺手重构与自己任务无关的文件。

Plan complete and saved to `onestep-control-plane/docs/plans/2026-05-15-onestep-control-plane-enterprise-readiness-three-releases.md`.

Recommended execution mode for this plan: `Subagent-Driven`, one agent per task package, one migration owner per release, one API contract owner per release.
