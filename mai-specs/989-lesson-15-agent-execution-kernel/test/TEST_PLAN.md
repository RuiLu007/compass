# 第 15 课｜Agent 执行内核验收

[返回需求简报](../BRIEF.md)

## 范围与环境

按 [PRD 的 AC-01～AC-15](../spec/PRD.md#9-验收标准)验收。本次基于 `9b62205` 与当前工作区的入口收口和回归修复。业务推理走 Web 的 `POST /api/web/v1/analysis/runs` 和 Agent 的 `POST /v1/analysis/runs`，使用合法会话与请求头验证两个旧 POST 均返回 404。模型管理、Provider 诊断、调用记录查询、取消和固定样本演示继续回归。

使用 Chromium、独立的 Web / Agent PostgreSQL 17 测试库，以及课堂发布库的专用测试克隆。Retail 样本为 `retail-dev-20261022b`，按发布摘要校验，运行时使用原发布的 `a_east` 受限身份。模型正向使用已启用的 DeepSeek Flash，异常和竞态使用受控上游及数据库锁。

## 现有入口与本次结果

| 层级 | 命令或用例 | 结果 |
| --- | --- | --- |
| Agent 单元及 HTTP | `pnpm test -F agent` | 240 项通过，包含旧入口 404、空文本工具轮续接、取消、预算和清理异常。 |
| Web 单元及 BFF | `pnpm test -F web` | 100 项通过，包含旧入口 404、Provider 诊断、流校验、会话与客户端竞态。 |
| Agent PostgreSQL 集成 | `pnpm test:integration -F agent` | 23 项通过，包含发布样本金标、数据库锁等待取消、后端 PID 退出及后继查询。 |
| Web PostgreSQL 集成 | `pnpm test:integration -F web` | 22 项通过，包含空库、旧库、权限、恢复和模型授权。 |
| 契约、静态检查与构建 | `pnpm contracts:check`、`pnpm check`、`pnpm build` | 契约无漂移，静态与测试编排 19/19、构建 7/7 通过。 |
| 全量浏览器回归 | `pnpm test:e2e -F web` | 49 项通过。恢复 Chat 复制观测编号后，受影响的 22 项分两轮复验通过。首次补跑有 3 项遇到测试账号登录限流，换独立测试库后这 3 项通过。 |
| 固定样本链路 | `pnpm smoke` 使用本次隔离 Web / Agent 地址 | 通过，覆盖 Web → BFF → Agent → 合成样本 PostgreSQL。 |

## 业务场景

| 场景与 PRD 对应 | 操作与通过条件 | 本次覆盖 |
| --- | --- | --- |
| TC-01 · 分析与下载（AC-01、AC-09、AC-10） | 用授权账号比较 2025-11-17 至 11-23 与 11-24 至 11-30。要求先查渠道，再根据结果查变化最大的渠道。核对 POS 的变化额 8,672.93 元、两轮卡片、自动折叠、CSV 来源与三个商品金额。重新展开后追问，历史只使用已完成回答。 | 真实浏览器、真实模型、真实 Retail，以及页面追问和文件边界用例。 |
| TC-02 · 工具批次及异常（AC-02～AC-05） | 对同一轮注入两条带独立 ID 的调用，再分别注入未知工具、非法参数、查询失败、不完整模型回复和执行异常。检查原 ID 配对、非法参数零查询、空数据与失败分型、终止后的派发计数，以及记录和凭据隔离。 | Agent 单元验证同轮两个有效 Retail 调用及一个未知调用。真实 PostgreSQL 区分查询超时与空结果，后继查询成功。 |
| TC-03 · 唯一入口与权限（AC-06、AC-08） | 发送固定与自动选模请求，验证每次发送只创建一个 Run。直接请求 Web 和 Agent 的旧 POST，均应返回 404。以无会话、无权限或伪造范围请求 Run，检查未进入 Agent 或 Retail。回归管理、Provider 诊断、调用记录查询、观测关联和固定样本演示。 | Agent OpenAPI 与 HTTP、BFF 权限及流校验、浏览器回归和 smoke。 |
| TC-04 · 连接中断（AC-11） | 工具卡出现后断开连接，再提交新任务。检查页面保留已展示过程，本轮回答和文件未提交，后续历史排除本轮，BFF 中止上游，Agent 收口在途资源。 | 浏览器关闭标签触发真实 BFF 与 Agent 中止，受控模型流退出。另验模型 401 安全收口及页面断流夹具。 |
| TC-05 · 模型停止（AC-12、AC-14） | 在模型生成中点击停止。检查停止回执、原流终态、上游退出、后续派发和未提交文件。覆盖首包前停止、越权、重复请求及两种完成竞态。 | 从真实浏览器停止受控模型，原流收到清理确认，迭代器关闭、在途与执行计数归零。首包前停止与竞态使用夹具。 |
| TC-06 · Retail 停止（AC-13） | 锁住 `core.channel`，由观察连接确认固定查询进入锁等待后发起停止。检查原 Run 终态、后端 PID 退出、无 CSV 和后续查询成功。 | 从真实浏览器停止真实 Retail SQL。观察到锁等待 PID 退出，停止轮无文件，下一轮查完渠道和商品并下载。 |
| TC-07 · 清理未确认（AC-15） | 注入流关闭异常或连接无法确认退出，发起停止。检查页面提示、未提交结果以及可用资源与隔离资源的边界。 | Agent 故障注入、Web 浏览器终态夹具。 |
| TC-08 · 课堂复现（AC-07） | 按[课堂复现说明](../../../docs/lesson-15-agent-run.md)完成正常查询与下载、两种在途停止和一次失败，用同一 Run ID 对照运行记录。 | 已跑正常查询、下载、模型停止、真实 SQL 停止和模型失败。全新发布的限制见下文。 |

## 发布与部署续验

| 项目 | 结果 |
| --- | --- |
| 旧课堂包全新发布 | 在空 PostgreSQL 17 集群使用与原包摘要一致的历史发布 CLI、63 表 DDL 和 `retail_0001`。49 项业务门禁及 47 项权限门禁通过。重生成的 1290 个文件与原包 manifest 一致。新发布的受限角色只见租户 101 / 区域 1，POS 两期金额及三个商品与独立 SQL 对账一致。 |
| Agent PostgreSQL | 使用独立 Agent 库和全新发布的 Retail 库运行 `pnpm test:integration -F agent`，23 项通过。其中 2 项 Retail 覆盖渠道与商品金标、真实 SQL 取消与后继查询。 |
| 空库容器部署 | 在独立 Compose 项目构建七个镜像，应用库从空卷启动。Agent 供应、迁移和 seed，Web 供应与迁移，五个应用启动及 smoke 均通过。Worker 收到停止信号后以状态码 0 退出。 |
| 容器 Run | Chromium 登录后，两个旧 POST 返回 404。新 BFF Run 端点返回有序 SSE，未配置模型时以安全失败终态结束，取消已结束 Run 得到 `already_terminal`。 |
| 真实 Langfuse | `observability_acceptance.py` 6 项通过，覆盖跨进程聚合、采样、Run 重试、首片段与导出故障。容器 Run 的响应头 Trace ID 可在真实 Langfuse 查询，问题正文未进入记录。 |
| Web PostgreSQL | 在容器 PostgreSQL 的独立测试库复跑，22 项通过。 |

主 PostgreSQL 的健康检查改为探测容器内 TCP 地址。空卷复验等到最终 PostgreSQL 实例就绪后才执行应用迁移。Langfuse 首次启动与镜像构建并行时超过了 Compose 的 240 秒等待窗口，随后完成首次迁移并通过健康检查和真实观测验收。

## 验收限制

- 当前发布 CLI 接收 `compass-retail-3`，本课的固定旧包需要配套的 `compass-retail-1` 发布工具和 DDL。此次使用本机留存的历史快照，现有 Git LFS 文件是另一版数据的扁平 SQL。
- 当前 CI 的 Agent 集成步骤缺少 `AGENT_RETAIL_TEST_DATABASE_URL` 与 `AGENT_RETAIL_TEST_ADMIN_URL`。现有浏览器回归还需要观测回包配置与 JEV 测试域名许可。本机验收分别注入独立 Retail 测试库、受控观测回包与 JEV 测试配置。
