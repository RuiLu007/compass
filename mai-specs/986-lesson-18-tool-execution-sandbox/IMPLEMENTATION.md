# 第 18 课实施记录

有效需求：[PRD](spec/PRD.md)，有效设计：[TECH_DESIGN](design/TECH_DESIGN.md)。用户于 2026-11-12 授权完整开发、单测和端到端验收。保留工作区已有的 M1～M3、界面与课程文档修改；实施前快照位于忽略目录 `work/lesson-18-implementation/`。

| 任务 | 交付                                                                        | 状态   |
| ---- | --------------------------------------------------------------------------- | ------ |
| T0   | `api/runtime/harness/workflows/tools` 模块迁移、进口更新、三项依赖边界检查  | 已实现 |
| T1   | 不可变 Registry、独立策略、统一 Dispatcher、内置查询、PNG Artifact          | 已实现 |
| T2   | OpenSandbox SDK、Python/collector 镜像、Ganesha 16 个独立 64 MiB NFS 工作卷 | 已实现 |
| T3   | owner/Run/Attempt/phase/seq 分配记录、跨副本配额、未知资源核对、0008 迁移   | 已实现 |
| T4   | 固定 Workflow 提案通过统一审批挂钩、执行树和工具生命周期事件                | 已实现 |
| T5   | 个人 MCP 配置与 BFF、密文凭据、分页发现、Run 冻结、取消和版本复查           | 已实现 |
| T6   | 串行/并行、整批预检与待审、预算计量、顺序回填、取消和隔离占用               | 已实现 |
| T7   | 单测、真实 PostgreSQL/容器、浏览器 E2E、构建和运行说明                      | 已验收 |

## 实现入口

- `runtime/service.py` 负责 Run 装配；`harness/executor.py` 负责推理循环，`harness/tool_runtime.py` 将内置业务处理器接入统一工具目录。
- `tools/contracts.py` 定义执行绑定、审批、副作用与批次策略，`registry.py` 保持声明和实际派发一致，`dispatcher.py` 与 `batch.py` 负责生命周期和调度。
- `tools/builtin/retail/` 为受信只读业务实现。现有 QuickJS 编排迁至 `workflows/script.py`，Python 分析独立使用 Sandbox。
- `tools/mcp/` 拥有个人连接、协议会话与版本校验；HTTP 接口位于 `api/mcp.py`，Web BFF 位于 `src/server/http/mcp-handler.ts`。
- `tools/sandbox/` 拥有控制面客户端与分配租约。计算容器删除确认后才创建只读取件容器；租约持久化，工作卷保留与计算配额分别管理。
- `api/runs.py` 提供 owner/Run 限定的 Artifact 读取与工作卷只读检查。前端显示真实图表、下载入口、MCP 来源和 Sandbox 执行环境。
- 本地管理员入口为 `pnpm sandbox init/up/status/logs/down`；[运行指南](../../docs/lesson-18-tool-runtime.md)列出环境、API 和测试命令。

## 验收对应

| AC    | 实现及验证覆盖                                                                                                                  |
| ----- | ------------------------------------------------------------------------------------------------------------------------------- |
| AC-01 | 严格 JSON、Schema、可见性、未知工具和整批预检；Dispatcher 单测及实际模型循环                                                    |
| AC-02 | 独立真实 Retail 测试库的受限角色、只读事务、查询范围与 SQL 取消                                                                 |
| AC-03 | 浏览器真实查询 → Python 计算 → PNG 校验 → 带权限读取与下载；完整授权数据快照作为输入                                            |
| AC-04 | Run 2 / 进程 4 的配额、预算计量、输入/结果/Artifact 上限；PostgreSQL 跨副本计算配额                                             |
| AC-05 | 查询取消、Python 活跃取消/CPU 超时、创建丢响应后的元数据核对；未确认时保留占用                                                  |
| AC-06 | 异常代码、内存超限、64 MiB 工作卷、过大 JSON、符号链接、非法 PNG、非 root 与网络策略                                            |
| AC-07 | 内部预算与 MCP 写请求进入 waiting；整批零派发；M4 保留待审状态                                                                  |
| AC-08 | 锁定 SDK/镜像、Compose 和供应脚本、ADR、启动及故障复验命令                                                                      |
| AC-09 | 本人配置加密与 owner 隔离；每 Run 发现、稳定别名、同一 Registry 声明/执行；无 Retail 的 MCP Run                                 |
| AC-10 | 真实 HTTP 夹具覆盖分页、重复游标、Schema、超时、可选/必需失败与目录超限；会话关闭断言；合并内置目录超限在路由及推理调用前拒绝   |
| AC-11 | 远端提示不授予本地权限；配置变更在预检、占槽后、执行期间和返回后复查；未知结局隔离占用                                          |
| AC-12 | 串行 stop/continue、并行上限与逆序完成、模型原顺序结果、待审和取消；Web 支持两项重叠查询事件                                    |
| AC-13 | 双租户独立卷、计算删除后重挂载、owner 拒绝、保留期清理与复用、不可变产物；控制面重启/NAS 中断复验、跨副本确认和执行环境占用隔离 |

## 已执行的检查

- `pnpm check`：19 个任务通过；Agent 336 项单测、Web 111 项单测，以及 home/memory/worker/data-seed 检查通过。工具脚本测试 48 项通过、13 项已有可选 hook 测试跳过。
- `pnpm build`：五应用及相关构建通过。五应用镜像顺序构建、独立 Compose 部署、Web → BFF → Agent → PostgreSQL、Home 分发素材、Memory 就绪及 Worker 正常信号退出通过；最终 Agent 镜像重建后的完整 smoke 和 QuickJS 执行再次通过。
- 0008 SQL 生成、漂移检查通过；0001～0007 迁移与实施前快照的字节一致。
- `COMPASS_SANDBOX_FAULT_TESTS=true pnpm test:integration -F agent -- -v`：57 项全部通过（约 308 秒），含 39 项数据库/模型/零售/Workflow 回归与 18 项工具、真实 Sandbox/NFS 验收，无跳过。目录预检调整后的 12 项 Run/Workflow 集成也单独通过。收尾租约保护新增 1 项数据库场景，并复验控制面重启、NAS 中断和创建丢响应，共 4 项专项回归全部通过（约 79 秒）。
- 浏览器 15 项分批通过：12 项 Composer 回归、1 项执行树交互与 2 项本课业务旅程。本课覆盖同轮并行 SQL → Python 出图与下载，以及个人 MCP 配置、计算、待审和取消；桌面、390 px 窄屏、键盘、复制与下载均已检查。首批 11 项通过后触发每分钟 10 次登录限制，余下 4 项以 `--last-failed` 启动独立测试服务后全部通过。收尾改动后，本课 2 项真实业务旅程再次单独通过（约 47 秒）。
- 文档相对链接检查、格式检查和 `git diff --check` 通过。最终命令汇总与截图保留在忽略目录 `work/lesson-18-implementation/`。

## 实施细节与边界

1. 沿用项目 MCP SDK 2.3.0，协议夹具固定握手 2025-11-25；OpenSandbox SDK 1.2.0。控制面、execd、egress 和 Python 基础镜像使用固定 digest。
2. NFS-Ganesha 使用 Docker Linux VM 内独立 ext4 loop 文件系统；每个 slot 64 MiB。计算使用非 root、1 CPU、512 MiB、64 PIDs、cap drop、no-new-privileges。网络设置为 `dns+nft` 并禁用 IPv6，覆盖域名与直连 IP。该模式的上游说明见 [OpenSandbox 配置](https://github.com/opensandbox-group/OpenSandbox/blob/main/server/configuration.md#egress)。
3. Python 命令最长 20 秒，完整创建/执行/删除/取件生命周期上限 106 秒，受 Run 剩余时间约束。远端 TTL 120 秒为失联兜底，创建回包未知不自动重试。删除回包丢失时，在原有 8 秒清理窗口内继续查询，确认资源不存在后再释放占用；窗口结束仍未知则隔离。未知租约及时进入核对；缺少 ID 且暂未发现资源时保留占用，找到资源并确认删除后释放。各 Agent 副本读取共享确认状态，归还自己的 Sandbox 槽；MCP 的未知槽单独保留。
4. 已接收 Artifact 在当前 Agent 进程保留 30 分钟供终态下载，最多 64 个 Run、每 Run 2 MiB。此保留窗口解决结果展示后的下载需求；NAS 工作卷另保留 24 小时。持久化 Artifact 与 Run 恢复由第 19 课承接。
5. 课堂 Docker 使用 runc；云端 gVisor 与托管 NAS 沿用既定方案，本次没有部署云资源。MCP 配置提供 API 与教学示例，完整管理页面及 OAuth 向导按原设计留待后续。
6. 浏览器验收中的模型输出由夹具固定，Web/BFF/Agent、PostgreSQL、MCP HTTP、Docker 与 NFS 使用真实组件。
7. 默认 8100 端口已有用户服务，五应用部署使用独立 Compose 项目和空闲端口。并行镜像构建期间出现 Docker 资源压力及数据库恢复，已改为顺序构建并重新验收。Sandbox 数据端口调整到 20000～29999，避免与 Linux 默认临时端口范围重叠。

临时浏览器夹具与独立五应用验收服务已停止，数据库数据卷保留；本次测试租约已清理归还，课堂 OpenSandbox/NFS 管理服务保留可用。日志和截图留在忽略目录。

后续首页补充：分析首页新增第 18 课提示词卡片，包含 Python 分析绘图、同轮多工具查询、本人 MCP 销售查询。Web lint、typecheck、111 项单测通过；浏览器检查覆盖三个问题的预填、键盘聚焦、工作流选择清除，以及桌面和 390 px 窄屏。临时验证服务已停止。

本次未提交或推送代码，未更新 BRIEF Phase 和 ROADMAP 阶段。
