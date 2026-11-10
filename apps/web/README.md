# Web / BFF

Next.js App Router + React + TypeScript。UI 与 BFF 在一个 Node 服务中部署。当前交付 [Feature 995](../../mai-specs/995-web-management-foundation/README.md)：本地账号、数据库会话、租户与工作区、直接成员、固定模板和个人权限、个人设置、通知与审计，以及原型完整导航框架。Feature 993 增加平台模型路由管理、测试与分析 Chat 模型选择；Feature 989 将 Chat 接入同步 Agent Run，展示多轮 Retail 工具过程、最终回答和 CSV，并支持终止。决策模型仍仅在管理端测试。通用文件上传、报告、看板、插件、定时及其余 Agent/Memory 管理保持待接入状态。

## 启动与账号

第 14 课在平台模型管理页增加“调用观测”、本实例导出状态与两模型只读对比；Trace 详情进入独立登录的本地 Langfuse。Chat / 测试收到响应头后显示本轮观测编号，取消和断流时保留。平台 API 沿用管理员鉴权，浏览器 Trace Context 不进入内部链路。部署与数据边界见 [观测运行说明](../../deploy/observability/README.md)。

从 Compass 根目录复制 `.env.example` 到 `.env`，安装依赖后执行：

```bash
pnpm db:up
pnpm db:provision -F web
pnpm db:migrate -F web
pnpm db:manage -F web -- create-user --login-name local.admin --display-name 本地管理员 --platform-admin true
pnpm dev -F web
```

账号工具从隐藏终端输入读取密码（12–128 字符），流水线须显式使用 `--password-stdin`；密码不作为命令参数。打开 <http://127.0.0.1:8100/login>，平台管理员创建租户时指定已有账号为初始租户管理员。平台管理员可直接进入所有有效租户及其工作区，获得完整模块与管理权限；登录后恢复最近工作区或首个可用入口，平台页保留完整导航。租户停用、空间归档、资源归属及个人资料/会话边界继续校验。

本地演示可显式设置 `WEB_FIXTURE_PASSWORD` 后执行 `pnpm db:seed -F web -- --fixture teaching`，初始化 `teaching.platform/admin/analyst/viewer/auditor/outsider` 六个合成账号及两租户三空间；重复执行不重置账号或覆盖业务修改。默认 seed 不写演示数据。禁止在共享环境使用此 fixture。

`WEB_DATABASE_URL` 仅供服务端运行；`WEB_MIGRATION_DATABASE_URL` 供迁移和受管账号工具；`WEB_PROVISION_DATABASE_URL` 仅供本地数据库供应。迁移与完整 SQL、权限、恢复见 [web/main](db/main/README.md)。`WEB_PUBLIC_ORIGIN` 必须与浏览器来源完全一致；正式环境使用 HTTPS，本地 HTTP 必须显式设置 `WEB_AUTH_ALLOW_LOCAL_HTTP=true` 并使用 loopback。会话固定有效期默认 12 小时，上限 12 小时。

受管工具另支持 `reset-password --login-name ...`、`set-status --login-name ... --status disabled|active`、`set-platform-admin --login-name ... --platform-admin true|false`，安全变更撤销该用户全部会话并保护最后有效管理员。`cleanup-sessions` 清理过期或撤销超过 30 天的会话。审计不自动清理；部署方须安排受控备份、容量监测和保留策略，普通运行角色不能修改或删除历史。

开发启动 `pnpm dev -F web` 会先停止目标端口的监听进程及当前 Web 目录的旧 Next 开发实例，再启动自身；先正常退出，5 秒后仍未退出则强制结束。默认端口 8100，支持 `PORT` 或 `-- --port 8105`。该入口依赖 macOS / Linux / WSL2 的 `lsof` 和 `ps`，会终止占用目标端口的其他应用。

页面地址使用稳定短标识，例如 `/t/yuanshan/w/huadong/analysis`。创建租户或空间时填写地址短标识；改名保持地址不变。旧 UUID 链接自动跳转，API 与权限上下文继续使用 UUID。分析侧栏仅保留新建按钮及按时间/主题的会话分组选项；侧栏顶部仅在分析模块显示“Compass / 分析空间”；其他模块直接显示一级导航标题，正文不重复标题。导航图标直接使用原型 SVG 图形，一级与二级菜单逐项映射；按钮、菜单行、浮层和移动抽屉按 [导航 UI 对照规范](../../mai-specs/995-web-management-foundation/design/NAVIGATION_UI.md) 维护。

## 工程边界

[服务端模块](src/server/README.md) 按 identity/access/audit/notifications 划分，运行时 SQL 和连接使用 `server-only`。受保护页面通过 Next App Router 局部导航，共享布局保留侧栏与顶部框架；搜索、分页、空间切换及保存后刷新保持 SPA 交互。首次进入与每次切页均校验当前权限后显示内容；断线、身份变化、超时立即清屏，恢复时重新加载文档。业务写入、审计与通知共用一个事务，普通查询使用可重复读快照，审计导出提交记录后才返回 CSV。

`GET /healthz` 检查进程，`GET /readyz` 检查配置及 Web 迁移版本；`/api/web/v1/*` 是严格校验的本地业务 API，响应 `private, no-store`。Web 基座运行只依赖自己的 PostgreSQL。Agent、Memory、Worker、IdP、模型和文件服务无需在线。

`/demo` 与 `/api/demo/overview` 保留原工程演示，需要 `COMPASS_DEMO_ENABLED=true`；后者经服务端 `AGENT_API_URL` 调用 Agent，超时或契约不兼容返回 503、始终 no-store。演示类型由根 `pnpm contracts` 生成，禁止直接修改 `src/generated`。Web build/typecheck 的静态契约生成仍需 Python 工具环境。

样式扫描限定在 `src/`，避免开发日志、测试产物与缓存变化触发热更新。

## 验证

```bash
pnpm lint -F web
pnpm typecheck -F web
pnpm test -F web
pnpm build -F web
pnpm db:sql:check -F web
```

数据库与浏览器测试必须提供专用的 `WEB_TEST_DATABASE_URL`、`WEB_TEST_MIGRATION_DATABASE_URL`，库名限定 `compass_web_test_*`；迁移与恢复另需 `WEB_TEST_ADMIN_URL`。测试会重置专用测试库，绝不使用日常库。`WEB_TEST_PG_CONTAINER` 可指定同一测试 PostgreSQL 容器执行 17 版 pg_dump/pg_restore；未设置时使用宿主对应工具。

```bash
pnpm test:setup -F web
pnpm test:integration -F web
# 浏览器使用另一专用测试库，并设置 WEB_TEST_PASSWORD
pnpm test:setup -F web -- --fixture teaching
pnpm --filter @compass/web exec playwright install chromium
pnpm test:e2e -F web
```

浏览器测试自行启动 8110 端口 Web，截图与失败 trace 放入忽略 Git 的 `work/995-playwright/`。只在已明确启动对应测试服务器时设置 `WEB_TEST_EXTERNAL_SERVER=true`。实际运行结果与验收映射见 [实施记录](../../mai-specs/995-web-management-foundation/IMPLEMENTATION.md)。

## 模型 Runtime 集成

平台入口 `/platform/models` 不要求租户成员身份；租户“Agent 与能力 → 模型路由”仅提供开放摘要。Chat 同时要求分析入口与 `models.invoke`，升级不自动授予新动作。BFF 在授权事务结束后调用 Agent，使用服务端 `AGENT_API_URL` 与 `COMPASS_AGENT_SERVICE_TOKEN`，不接收浏览器声明的内部身份。Provider 凭据只由 Agent 加密保存，Web 数据库不保存副本。配置步骤见 [Agent 模型接入](../agent/README.md#模型接入)。

模型浏览器测试需要额外启动 `apps/agent/tests/e2e_app.py`，配置专用 Agent 测试数据库、同一服务令牌和加密密钥，并使 `AGENT_API_URL` 指向该宿主。它只替换上游 HTTP，Web、BFF、Agent API 与数据库均实际运行。Playwright 使用 `.next-e2e` 独立输出目录，保留日常开发服务。真实供应商验收单独记录在 [993 实施记录](../../mai-specs/993-lesson-11-llm-runtime/IMPLEMENTATION.md)。

模型目录复用 LiteLLM 支持的生成模型协议，添加 Provider 支持手工 `provider/model` 标识。Base URL 留空使用 SDK 默认；API Key 与供应商连接参数均只写不回显。连接参数 JSON 可配置 Azure 版本、Bedrock 区域及原生认证等，留空保留，`{}` 清空。Chat 按生成模型类型、启用状态、平台可见性和用户权限开放。

## 切换应用与页面状态

切换 App 或标签页时界面、滚动位置、草稿、附件、聊天与在途生成保持原样。离开和返回不触发遮蔽、禁用、刷新或恢复复验；定时会话检查静默执行，临时失败不影响当前界面。登录失效、权限变更、离线及文档离开仍清理旧内容；页面内存不跨整页刷新持久保存。

## 意图分流与响应

分析输入框可选择静态主模型或“系统优化”，每轮仍只向 `models/calls` 发起一次请求。Agent 返回两级 `intent`、字典 `features`、契约 `routing`、Runtime `result` 与 `metrics`；回答下方“意图分类与响应结构”可展开查看。闲聊命中 `SC-39` 后使用独立 prompt 调用 Flash 婉拒，并询问业务数据任务。

BFF 与页面分别校验 `RoutedModelEvent` / `RoutedCallResponse`，实际 Provider 在 `route_selected` 后绑定；Provider 诊断继续沿用 `ModelEvent` / `CallResponse`。生成期间可停止，失败和不完整回答不进入成功上下文；分类详情仅保留在本页。浏览器验收位于 `tests/e2e/intent-routing.spec.ts`，使用独立Web/Agent PostgreSQL库及受控上游宿主。

## Agent Run 与 Retail 分析

分析 Chat 的发送入口为 `POST /api/web/v1/analysis/runs`，停止入口为 `POST /api/web/v1/analysis/runs/{runId}/cancel`。两者复用同源、CSRF、有效工作区、`module.analysis` 与 `models.invoke` 检查；授权事务提交后，BFF 才携服务端可信主体请求 Agent。浏览器不能传内部身份、Retail 账号或连接信息。

BFF 严格校验 `compass.run` v1 的 UTF-8 SSE、包 Schema、序号、Run / Step / ToolCall 生命周期、唯一终态、文件来源和干净 EOF，再重新编号转发。页面按 Step 显示工具卡，只有成功终态在完整 EOF 后提交 `finalAnswer`、历史和 Blob 文件。重置对话、退出、权限版本变化或范围切换会中止活动请求并撤销下载 URL。

当前部署要求 Agent 单进程、单副本。Retail 发布绑定只配置在 Agent；Web 只使用当前会话范围。课堂配置、对照 SQL 和停止演示见 [第 15 课复现说明](../../docs/lesson-15-agent-run.md)。

## 可靠性模拟测试

平台管理员进入 `/platform/models`（Agent 与模型）即可运行 11 个固定场景：正常、429/503 恢复、次数耗尽、认证失败、超时、半途断流、Schema 拦截、取消、并发过载及关闭失败隔离。页面展示真实事件、预览、尝试摘要、未知用量、容量及可提交结果；无需模型密钥。普通租户用户不能访问模拟接口。

Chat 与 Provider 诊断均在唯一 final 及干净 EOF 后提交成功结果；失败时清除嵌套终态与续接 token。普通调用详情展示 execution 摘要，旧记录没有明细时保持原显示。浏览器验收位于 `tests/e2e/reliability.spec.ts`，使用独立 PostgreSQL 测试库及 Agent。
