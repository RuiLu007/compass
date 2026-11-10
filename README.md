# Compass

电商经营分析 Agent；《企业级Agent工程架构实战营》的贯穿项目。产品名 **Compass**，工程名 `compass`，包命名空间 `@compass/*` / `compass-*`。

当前交付包括 **Web 工程基座与完整界面框架**，并保留 M0 工程链路：Turborepo + pnpm + uv，Next.js 前端与 BFF、FastAPI、PostgreSQL、Alembic、契约生成、测试与容器构建。通过合成经营数据验证 Web → BFF → API → 数据库链路。

产品、PRD、设计与交互原型统一维护在 [Feature 999 需求目录](mai-specs/999-compass-product-course-landing-app/README.md)。工程实施决策见 [ADR](docs/adr/0001-workspace.md)。开发源码维护在 [shanggqm/compass](https://github.com/shanggqm/compass)，仓库为私有，初始化需访问权限。课程仓库通过子模块 `learner-kit/code/compass` 固定开发版本。首个脚手架交付标签为 [v0.0.1](https://github.com/shanggqm/compass/releases/tag/v0.0.1)。

## 本地启动

需要 Node.js 24（验收版本见 `.node-version`）、pnpm 11.3.0、uv 0.13.0，以及运行中的 Docker Desktop。uv 根据 `.python-version` 获取 Python。Windows 使用 WSL2。

从本目录执行：

```bash
cp .env.example .env
pnpm install --frozen-lockfile
pnpm setup
pnpm doctor
pnpm db:up
pnpm db:provision -F web
pnpm db:provision -F agent
pnpm db:migrate
pnpm db:seed
pnpm db:manage -F web -- create-user --login-name local.admin --display-name 本地管理员 --platform-admin true
pnpm dev
```

打开 <http://127.0.0.1:8100/login> 使用刚创建的账号登录；受管工具会提示隐藏输入密码。原链路演示位于 <http://127.0.0.1:8100/demo>。API 文档位于 <http://127.0.0.1:8102/docs>。在另一个终端执行 `pnpm smoke` 验证整个链路。停止开发服务使用 Ctrl+C；`pnpm db:down` 停止数据库并保留数据卷。

默认端口：home 8101、Web 8100、agent 8102、memory 8103、PostgreSQL 55432；worker 不监听 HTTP 端口。 Langfuse 本机入口为 8104，Web 浏览器测试使用 8110；8105–8109 预留。启动前确认端口空闲。`.env` 只由根任务脚本加载；绕过根入口直接运行子应用脚本或 `next start` 时需显式注入环境。不要把 `.env` 复制到 Web 目录或提交 Git。

根命令通过 Turborepo 筛选子应用。统一规范为 `pnpm <任务> -F <应用短名>`，短名从 workspace 的 `package.json` 包名自动解析，新增 app 无需登记别名：

```bash
pnpm dev -F home       # 只启动官网，端口 8101；只需 Node.js 与 pnpm
pnpm dev -F web        # 只启动 Web / BFF，仍加载根 .env
pnpm dev -F agent      # 推理后台，需要 uv 与 Python 环境
pnpm dev -F memory     # 记忆服务脚手架，端口 8103
pnpm dev -F worker     # Python 后台进程脚手架，无 HTTP 端口
pnpm build -F home     # 只构建官网，使用 Turbo 缓存
pnpm typecheck -F home
```

`pnpm dev` 启动全部五个应用；`pnpm dev -F home -F web -F agent` 只启动基础环境。支持 `-F home`、`--filter home`、`--filter=home` 和重复筛选；`-F '!home'` 排除对应应用。完整包名 `-F @compass/home` 和目录 `-F ./apps/home` 继续可用。依赖图、glob、Git 等复合 Turbo 筛选保持原生语法，其中包名写完整，如 `-F '@compass/web...'`。`--` 后面的参数直接交给子应用。

`pnpm -F home dev` 由 pnpm 直接调用子应用脚本，会绕过根环境加载及 Turbo 编排；日常使用上面的统一入口。新增应用必须遵守 [应用准入规范](apps/README.md)，短名冲突会报错，`pnpm check` 会自动验证所有 app 的短名启动入口。

## 检查与构建

```bash
pnpm check           # 格式、工程脚本、契约漂移、依赖边界、lint、类型检查和单元测试
pnpm format          # Prettier + Ruff 自动格式化
pnpm format:check    # 只检查格式，不修改文件
pnpm build           # 生成契约，构建 Next.js standalone 和 Python wheel/sdist
pnpm contracts       # Python DTO → OpenAPI / JSON Schema → Web TypeScript
pnpm contracts:check # 重新生成到临时目录，检测漂移，不改工作文件
pnpm test:tooling    # 验证任务转发、工具链隔离与 Turbo 依赖图
```

完整工程初次安装后先运行 `pnpm setup`；只开发 home 无需此步骤。数据库相关任务和网络 smoke 不缓存；构建和静态测试缓存包含实际工具链版本，Python 版本指纹仅影响三个 Python app、数据准备工具与契约任务。开发任务使用 `.env`，缓存任务不加载 `.env`。Web 的 build / typecheck 通过 Turbo 依赖根 contracts 任务；home 不触发契约生成。契约生成无需数据库、模型密钥或运行中的 API。

数据归属与 SQL 迁移遵循 [全项目规范](docs/database-migrations.md)：Web / BFF 负责账户、权限与通用配置等工程 CRUD；Agent 负责推理运行及相关状态；各 app 按逻辑数据库独立管理迁移、完整 SQL 与增量交付。Web 与 Agent 分别使用独立迁移、运行角色和 SQL 交付，见 [web/main](apps/web/db/main/README.md) 与 [agent/main](apps/agent/db/main/README.md)。`pnpm db:migrate` 会运行 Web 与 Agent 已登记迁移；用 `-F web` 或 `-F agent` 明确选择目标。

编码风格与排除范围见 [编码规范](docs/coding-style.md)：前端使用 ESLint + Prettier，Python 使用 Ruff，跨编辑器使用 EditorConfig。以 Compass 根目录打开 VS Code / Cursor 并安装推荐扩展后，保存时自动格式化；仅开发前端可使用 `pnpm format:frontend`，无需 Python。

Agent 与编辑器配置保存在本地的 `.agents/`、`.claude/`、`.codex/` 和 `.vscode/`。全新检出后可按需配置编辑 Hook 和编辑器。项目的格式化命令与规则见[编码规范](docs/coding-style.md)。

## 容器启动

从本目录执行：

```bash
pnpm run deploy        # 构建、迁移、启动并验证全部五个应用
pnpm run deploy home   # 仅部署指定应用；也可选 web / agent / memory / worker
pnpm deploy:status     # 查看状态，可追加 app 名称
pnpm deploy:logs web   # 查看最近日志
pnpm deploy:down       # 停止整个 Compose 项目，保留数据卷与镜像
```

`deploy` 与 pnpm 内置命令重名，必须写 `pnpm run deploy`。入口固定本机 Unix socket 与 Compose 项目，拒绝远程 Docker；已有开发进程占用端口或数据库不健康时提前失败。Web 先检查契约一致性，修改 DTO 后先执行 `pnpm contracts`；home 先生成分发素材。镜像构建完成后才停止所选应用，每次部署显式执行所属迁移，再启动并等待健康检查及 smoke。默认地址为 home 8101、Web 8100、Agent 8102、Memory 8103。

部署不读取根 `.env`，本地连接由 Compose 声明；不同应用独立选择，数据库按需启动，已有数据保留。首次 Web 登录账号仍需受管命令显式创建。要求 Node / pnpm、Docker Desktop；部署 Web 另需已有 Python 契约环境。失败退出非零，保留现场；详情及恢复边界见 [部署说明](deploy/README.md)。

## 工程结构

| 目录                                 | 职责                                                                        |
| ------------------------------------ | --------------------------------------------------------------------------- |
| [apps/home](apps/home/README.md)     | 独立产品官网与课程指南，Next.js / React / TypeScript / shadcn/ui，端口 8101 |
| [apps/web](apps/web/README.md)       | Next.js App Router、页面、BFF、生成类型与网络响应校验                       |
| [apps/agent](apps/agent/README.md)   | FastAPI、运行相关数据、应用迁移及三天演示样本                               |
| [apps/memory](apps/memory/README.md) | FastAPI 记忆服务脚手架，独立健康检查与构建                                  |
| [apps/worker](apps/worker/README.md) | Python 后台进程脚手架，信号退出与独立构建                                   |
| [packages](packages/README.md)       | 共享包准入规则；达到两个 app 消费时才提取                                   |
| [contracts](contracts/README.md)     | 从 API DTO 生成的版本化网络契约                                             |
| [data](data/README.md)               | 已验收学员数据、Git LFS 压缩包与初始化说明                                  |
| [deploy](deploy/README.md)           | Compose 与每个 app 的镜像入口                                               |
| [tools](tools/README.md)             | 独立离线工具；业务数据生成、校验、导出与候选发布                            |
| [scripts](scripts/README.md)         | 契约、工具链、依赖边界与 smoke                                              |
| [docs](docs/README.md)               | 工程决策、验证记录与后续阶段边界                                            |

本仓库的 [Compass workflow](.github/workflows/compass.yml) 从仓库根目录运行。通过课程仓库开发时，从课程根目录执行 `git submodule update --init learner-kit/code/compass`。先在 Compass 创建或切换开发分支，提交并推送代码，再从课程仓库提交子模块指针。课程 CI 验证指针固定的提交。

## 课程完成节点

课程完成节点使用附注标签，名称统一为 `compass/LNN-complete`：`NN` 为至少两位的正式课课次编号，例如第 11 课为 `compass/L11-complete`。标签指向本课代码与学习资料的交付提交，附注记录课次、交付范围及保留事项。课程节点标签与 `vMAJOR.MINOR.PATCH` 正式版本分别管理；已有标签保留原指向。

## 当前能力边界

- 示例接口显式启用 `COMPASS_DEMO_ENABLED=true` 后才开放，数据全部为固定合成数据。接口不接受租户、SQL 或自由代码输入；尚未接入真实经营数据。
- 本地登录、数据库会话、租户与成员、个人权限、CSRF、设置、通知和审计已接入；企业 OIDC、运行编排、Sandbox、EvalOps 留待后续 Feature。模型 Runtime 的管理、测试和 Chat 调用见 [993](mai-specs/993-lesson-11-llm-runtime/BRIEF.md)。共享部署需配置 HTTPS、私有凭据及备份策略。
- Python 应用已创建：`agent`（原 agent-api）、`memory`、`worker`。memory 当前仅有服务健康检查，worker 仅有进程生命周期；普通分析 Runner、记忆业务、Temporal / Outbox 与共享执行包仍按 [实施任务](mai-specs/999-compass-product-course-landing-app/design/scaffold-design.md#8-部署收敛实施任务)推进。
- 当前 Web 已搭齐原型框架；分析 Chat 已接入本页对话、两级意图分类、字典特征和静态/自动选模；闲聊通过专用 Flash prompt 引导回业务数据任务，响应结构可在回答下方展开，模型接入提供平台管理与租户只读摘要；除第 15 课固定 Retail 工具外，通用数据查询、文件上传、报告、看板及其余 Agent/Memory 业务继续保持待接入状态。实现及验收见 [995](mai-specs/995-web-management-foundation/README.md)。
- 第 15 课已将分析 Chat 接入进程内 Agent Run：一次发送可顺序完成多轮固定 Retail 只读查询、展示工具过程、提交最终回答与有界 CSV，并能按 Run ID 终止在途模型或数据库查询。该能力采用单 Agent 进程、单副本和内存态；课堂配置及复现见 [Agent Run 说明](docs/lesson-15-agent-run.md)。
- 本工程没有授予开源许可证；依赖许可证由各上游项目提供。

## 独立产品官网

运行 `pnpm dev -F home`，打开 <http://127.0.0.1:8101>。官网包含产品与课程说明、按课号组织的课程资料、绿色 GhostFibers 首屏和原型入口，无需 Python、数据库或模型密钥。生产构建用 `pnpm build -F home`；`dev:home` / `build:home` 保留为同一 Turbo 入口的快捷命令。详见 [home 运行说明](apps/home/README.md)。

## 合成零售业务数据

已验收的 111,055 单诊断数据保存在 [`data/retail/`](data/retail/README.md)，约 117 MiB 的压缩包通过 Git LFS 分发。先安装 [Git LFS](https://git-lfs.com/) 并执行 `git lfs install`，再克隆仓库。已有克隆在 Compass 根目录执行：

```bash
git lfs pull --include="$(git rev-parse --show-prefix)data/retail/compass-retail.sql.gz" --exclude=""
pnpm data:prepare
```

`data:prepare` 校验压缩包与解压 SQL，生成 `data/retail/compass-retail.sql`；仅需 Node.js / pnpm，无需 Python、数据库或重新生成数据。完整导入命令见 [学员初始化步骤](data/retail/README.md#导入)。`pnpm db:migrate -F agent` / `pnpm db:seed -F agent` 初始化三天工程样本，与完整零售数据入口分开。

`pnpm data:seed` 调用 [tools/data-seed](tools/data-seed/README.md) 的独立 Python 工具，提供 Feature 998 的业务包生成、校验、候选库装载和发布入口。支持边界小样、1 万单 dev、10 万单工程 demo 和固定机会量的 diagnostic；提供学员单文件 SQL 导出、固定业务历史、十二类场景输入和源侧只读权限。使用专用零售库配置，原 `pnpm db:seed` 保留。

命令、交付目录、参考时钟和失败回切见 [业务数据操作说明](docs/data-seeding.md)；实际验证记录见 [998 实施记录](mai-specs/998-business-data-seeding-tool/IMPLEMENTATION.md)。

## 可靠模型执行与课堂演示

Feature 991 为主生成增加有界恢复、共享并发容量和安全尝试摘要。平台管理员可在“Agent 与模型”页的“可靠性模拟测试”运行固定故障场景，无需厂商密钥。数据库升级至 Agent 0004；[Feature 991 技术方案](mai-specs/991-lesson-13-reliable-model-execution/design/TECH_DESIGN.md)登记行为和验收范围。

## 模型调用观测

Feature 990 增加 OTel 请求链路、Logical Call / Attempt、调用编号复制、平台观测状态与固定模型对比。默认关闭；本地后台采用 Docker Langfuse，使用 `pnpm observability init`、`pnpm observability up` 初始化并启动，页面在 <http://127.0.0.1:8104>。部署、账号配置、数据格式/存储和多实例传播见 [Langfuse 运行说明](deploy/observability/README.md)。本期无业务库迁移，Agent 仍为 0004；观测故障隔离、敏感正文排除和实际验收见 [990 实施记录](mai-specs/990-lesson-14-model-observability/IMPLEMENTATION.md)。

[第 14 课学习资料包](mai-specs/990-lesson-14-model-observability/lesson-14-learning-materials.zip)包含 PRD、Markdown 技术方案与离线 HTML。

## Agent 执行内核

Feature 989 在既有模型路由与可靠执行上增加同步 ReAct 循环、Run / Step 控制、`compass.run` v1 进度流、固定 Retail 工具、CSV 和全链路终止。Web 仍负责会话、工作区与双权限校验；Agent 根据可信范围和部署绑定使用独立只读身份。架构取舍见 [ADR 0002](docs/adr/0002-agent-execution-kernel.md)，配置、对照数据、正常与故障演示见 [课堂复现](docs/lesson-15-agent-run.md)。
