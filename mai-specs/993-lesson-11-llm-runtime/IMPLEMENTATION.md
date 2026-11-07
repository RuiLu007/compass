# 993 实施记录

本次授权：按 my-implement-code 完整实现，随后按 my-review-code 审查、修复并完成单元、集成与端到端测试。有效范围见 [PRD](spec/PRD.md)、[技术方案](design/TECH_DESIGN.md)及[设计评审](review/design_review.md)。Chat 仅开放两款 DeepSeek LLM；阿里云决策模型仅管理端测试。

## 当前状态

2026-11-04 按用户授权实施 [V2 技术方案](design/TECH_DESIGN.v2.md)。Runtime、ModelService 与 HTTP 的职责迁移及两个适配器统一入口已完成，当前本地检查、数据库集成、完整浏览器回归和构建均通过；本轮详细结果见下方 V2 实施节。阿里云决策模型按用户安排后测，保留 AC-993-16/24 的真实供应商验收事项。保留原有工作区修改，未提交或推送；Feature Phase 保持 draft。

以下 T01～T08 与 2026-11-03 章节保留既有实现、独立评审及真实联调记录，V2 本轮结果单独登记。

| 工作项               | 当前结果                                                                                     | 验证状态                                                                                                  |
| -------------------- | -------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| T01 依赖与协议       | 锁定 litellm-core 1.106.6；每请求 HTTP client、Thinking 强度、单次出站、解压和工具元数据映射 | 受控 HTTP 与两款 DeepSeek 真实调用通过；决策模型后测                                                      |
| T02 数据库           | Agent 0002、Web 0003；独立角色、受管供应/迁移、完整与增量 SQL                                | 空库、旧库、SQL、权限、恢复与 Agent ORM 一致性通过；旧0001接管、未知sequence保护及schema/PK漂移拒绝均通过 |
| T03～T04 API/Runtime | 平台配置/目录/测试，可信上下文，租户双权限，幂等/乐观锁，调用元记录                          | Agent/BFF 单元和 PostgreSQL 业务集成通过                                                                  |
| T05～T07 UI/Chat     | 管理、能力测试、Chat、DocumentSession 取消/撤权，生成 DTO/Schema                             | 完整浏览器 25/25 通过，0 跳过、失败或不稳定用例                                                           |
| T08 完整验证         | 全局检查与构建、独立代码审查及修复、隔离容器部署                                             | check/build、独立复评、隔离容器与 smoke 通过                                                              |

## 2026-11-03 基线检查

- `pnpm check`：57 项工程工具测试、格式、脚本 lint、契约漂移、workspace 边界及全部应用 lint/typecheck/test 通过。应用单测：Web 43、Agent 61、Home 19、Memory 1、Worker 2、data-seed 34。A05/A06 后再次执行通过，日志 `work/993/live-check.log`；此前完整检查记录保留。
- `pnpm build`：7 个构建/契约任务通过，日志 `work/993/build.log`。
- Web PostgreSQL：19 项通过，覆盖 0001/0002 升级至 0003、权限不自动授予、备份恢复、平台/租户权限和出站前释放事务；日志 `work/993/web-integration.log`。
- Agent PostgreSQL：正式 `pnpm test:integration -F agent` 12 项通过，包含原生/SQL空库与升级、受限运行角色、恢复后凭据解密、ORM一致性、真实事务与并发幂等；日志 `work/993/agent-integration.log`。
- BFF 只读模型列表 view 参数修复后，定向单测 13 项通过。
- 最终 Webpack 浏览器：25/25 通过，0 skipped/unexpected/flaky；开始时间 UTC 2026-11-02 18:53:15.003，耗时176.8秒，JSON为 `work/993/full-playwright-results.json`。已人工复核375/768/1440管理、测试与Chat截图。
- A04元记录等待时序问题已稳定复现并修复，失败轮次另存 `work/993/webpack-ledger-failure-results.json`；保留失败记录，最终结论采用上述修复后全套结果。
- Compose 配置校验、`git diff --check` 已通过；五应用镜像已在独立项目 `compass-993-test` 构建并健康启动。smoke覆盖Web鉴权、Web→BFF→Agent→PostgreSQL、Home、Memory；模型API认证和离线目录测试通过。A04后重新构建Agent并再次通过健康检查与smoke，Worker正常退出已验证。测试服务已停止，数据卷保留；日常开发服务保持原状。

## 审查与修复

独立 SubAgent 已审查 Web/BFF/UI 与 Agent Runtime；数据库/部署/MVP范围已独立审查并复评。已修复停用配置测试入口、Chat 错误终态展示、失效模型选择恢复、只读列表 view 透传、工具名分片拼接、同步输出上限及 SDK 对正文 `[DONE]` 字样的误判。此前 14 项发现已关闭，A04 独立 3 项回归及完整浏览器 25/25 通过；本轮真实联调的新增发现见下文，正式记录见 [代码评审](review/code_review.md)。

真实联调补充 A05/A06 后，累计 16 项已确认发现全部关闭。A05 在解析前解码供应商 gzip 响应，保留即时 SSE 和解压后大小校验；A06 显式映射同步工具的公开字段，兼容 SDK 附带的 index，入站 DTO 继续严格校验。新增 11 项回归后 Agent 单测 61/61，lint/typecheck 通过；独立复核压缩流取消、工具续接和非法参数均通过。

实现时还修复了 PostgreSQL 索引列名、同事务 Provider/变更记录写入顺序、并发 PATCH 幂等重查，以及 SDK DeepSeek 的 HTTP 注入方式。每项由针对性测试覆盖。

## 发布与恢复

数据库所有者分别为 `agent/main` 和 `web/main`，迁移源及 SQL 真源见 [Agent 数据库](../../apps/agent/db/main/README.md)与 [Web 数据库](../../apps/web/db/main/README.md)。顺序为备份与供应 → Agent 0002 → Web 0003 → 两端应用 → 服务令牌/加密密钥/允许端点 → 平台接入与显式租户授权。各库独立事务。恢复使用新供应库、原备份和原加密密钥，验证后切换连接，保留旧库。

CI 增加 Agent 真实 PostgreSQL 验证和显式测试宿主，运行完整浏览器用例；生产入口没有 Mock 开关。测试数据、运行日志、数据库凭据与截图位于忽略目录 `work/993/`，不进入源码或本文。

## 2026-11-03 DeepSeek 真实联调

用户授权先使用 DeepSeek 凭据，决策模型后测。本轮使用隔离的 Agent/Web 数据库及本机 8013/3013 端口，生产 `compass_agent.main:app` 连接 `https://api.deepseek.com`；真实阶段没有注入受控 Provider。既有开发服务保持原状。

- 两款模型实际返回 `deepseek-v4-pro`、`deepseek-flash`。每款 10 次、共 20 次调用全部 completed，逐项核对同 call_id 的数据库元记录、配置版本、供应商请求标识及用量。
- 每款覆盖同步、流式、Thinking low/high/max、JSON 输出、JSON 输出加本地 Schema 校验、同步/流式工具声明及手动结果续接。工具流收到 13 个分片，续接返回预期合成结果；无业务工具执行。
- 页面验收 6/6：两款模型分别管理端同步测试，Chat 分别完成两轮流式对话并正确使用上下文。重载后配置仍在，密钥编辑框留空，读取响应无密钥；数据库凭据密文及可解密性已核对。
- 最终 Runtime 20 次合计 input 3936、output 462 tokens；页面 6 次 input 128、output 14。另有 1 次页面采集失败但调用成功，input 19、output 2，单独登记。以上仅为最终验收批次用量，未计入排查与失败轮次的全部消耗。
- 安全结果：`work/993/live-deepseek-results.json`、`live-browser-results.json`；初轮失败保留为 `live-deepseek-initial-failure.*`、`live-deepseek-tools-failure.*`、`live-browser-initial-failure.json`。凭据仅在本机受限文件及 Agent 加密字段，日志与文档未包含原值；结果不含原始 Thinking。
- 修复后 `pnpm check` 19/19 任务通过（应用单元 160 项，工具 57 项，共 217 项）；Agent PostgreSQL 12/12 再次通过；`pnpm build -F agent` 通过。日志分别为 `work/993/live-check.log`、`live-agent-integration.log`、`live-agent-build.log`。此前 Web PostgreSQL 19/19、全量构建与隔离容器验证保留，以上两处 Runtime 修复没有修改 Web、契约或迁移。
- 最终完整浏览器回归再次 25/25 通过，0 跳过、失败或不稳定用例，耗时 184.4 秒（北京时间 2026-11-03 09:14:31 开始）；结果 `work/993/live-final-playwright-results.json`。该轮显式切回受控 HTTP 测试宿主，与上面的真实供应商 20+6 次分别记录。独立测试服务已停止，数据库和私密配置保留，普通开发服务不变。

## 2026-11-03 分析输入框交互

用户确认先完成界面交互，后台能力后续接入。按 DeepSeek 参考截图调整输入区：新建居中加高，开始对话后底部停靠；上方历史独立滚动，模型选择在左下，附件与发送/停止在右下。附件卡片位于顶部单行轨道，最多 10 个，提供溢出箭头、删除及重复选择。支持模型默认开启 Thinking/high，保留 Enter 发送、Shift+Enter 换行和中文输入法保护。

实现位于 `apps/web/src/components/analysis-composer.tsx`、`analysis-attachments.tsx` 及分析页面专属 CSS。附件只保存在本页内存，选中后明确提示暂不支持发给模型，并阻止带附件发送；上传解析、联网搜索和服务端会话继续留待后续接入。两态布局依赖本页对话状态，新建、离开及撤权统一清理。没有新增 DTO、数据库或 Agent Runtime 变更。

- 独立 UI 审查发现并关闭 2 项问题：删除最后附件时保留已有对话的离页提醒；矮屏输入区提供有界滚动，使按钮与附件说明可触达。正式结论见[代码评审](review/code_review.md)。
- 新增 `apps/web/tests/e2e/analysis-composer.spec.ts`，6/6 通过，0 失败或跳过；最终矮屏修复后定向复测 1/1 通过。覆盖布局切换、历史滚动、附件上限/滚动/清理、发送阻止、Thinking 参数、键盘、离页提醒及 375/768/1440 宽度与 375×300/350 矮屏。
- `pnpm lint -F web`、`pnpm typecheck -F web`、`pnpm test -F web` 全部通过，Web 单元测试 43/43。交互测试使用真实 Web 登录与受控模型响应，没有请求真实供应商。
- 既有交互回归 3/3 通过，0 失败或跳过：分析侧栏/输入框、可读路由与租户隔离、Chat 选模/停止/撤权清屏。使用独立数据库及真实 Web → BFF → Agent API 与受控上游，结果 `work/993/composer-regression-playwright-results.json`，耗时 37.9 秒。测试服务已停止，开发服务保留。
- 本机记录：`work/993/composer-playwright-results.json`、`composer-short-playwright-results.json` 与对应日志；11 张截图为 `composer-{empty,attachments,started}-{375,768,1440}.png` 和 `composer-started-short-{300,350}.png`。测试服务与日常开发服务隔离，3000/8000 开发入口继续运行。

## 2026-11-04 V2 实施与验收

本轮授权为按 my-implement-code 完整实现 V2，并完成单元、集成与端到端验收。实施依照 V2-01～V2-05，不新增路由、重试、fallback、会话持久化或共享包；Agent 迁移保持 0002，Web 保持 0003，公共 DTO 与 SSE 事件保持兼容。

| 任务               | 已交付内容                                                                                                                  | 验收覆盖                                                                       |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| V2-01 固定类型边界 | ModelSpec、ProviderAccess、ExecutionRequest、RuntimeResult、CallPolicy、PreparedCall、RuntimeFailure 与窄 ContinuationCodec | 无应用反向依赖，嵌套 DTO 隔离，敏感字段 repr 隐藏，契约无漂移                  |
| V2-02 统一 Adapter | 两种适配器共同实现 invoke(request, session)，单次 HTTP、协议 EOF、解压和工具续接逻辑保留                                    | 生成与决策同步/流式/工具/Thinking/JSON/Schema；旧格式续接与过期验证            |
| V2-03 收拢生命周期 | Runtime 管期限/取消/资源，Service 管占位/任务/记录，API 管队列和唯一 final                                                  | 首包前取消、流中取消、重复取消、回压超时、准备提交竞争、落库等待竞争和幂等终态 |
| V2-04 能力解析     | 具体模型能力解析与 Chat 准入分开，unknown 只供平台诊断                                                                      | 模型/适配器绑定验证、无支持能力时零出站、诊断与 Chat 准入                      |
| V2-05 验证与说明   | 独立内核及应用生命周期测试，两个模块 README，Agent 说明与本实施记录                                                         | 单元、真实 PG、完整浏览器、契约、全项目检查与构建                              |

实现细节补充：`RuntimeResult.upstream_sent` 是内部字段，用于元记录写入失败时准确计算 `upstream_outcome_unknown`，未加入 HTTP Schema；ProviderProgress 的访问属性只操作统一 output，不建立第二份结果状态。CallPolicy 验证有限时间及限制上界；应用在完整结果映射后检查 2 MiB envelope。准备阶段也受绝对期限保护，线程池事务的提交未确认时保持零出站。

本轮修复的回归问题：正常取消断连监测任务会误取消调用，现按拥有者任务的取消计数区分；终态重复写入仅接受相同记录；SDK timeout 使用预算剩余时间；续接序列化统一复用 canonical；Web 初始化命令直接加载 TypeScript 时缺少 `.ts` 扩展名，已修复相应导入并通过独立数据库初始化。

本次执行结果（日志与私有测试配置保存在忽略目录 `work/993/v2-implementation/`）：

- `pnpm check`：19/19 任务通过，包含 57 项工具测试，Agent 86、Web 43、Home 19、Memory 1、Worker 2、data-seed 34 项应用测试；合计 242 项。格式、lint、类型、依赖边界与契约漂移检查均通过。日志 `check.log`。
- `pnpm test:integration -F agent`：真实 PostgreSQL 14/14 通过，包含空库/旧库/原生与派生 SQL、运行账号权限、恢复、并发幂等，以及 V2 准备提交后取消和终态一致性。日志 `agent-integration.log`。
- `pnpm test:integration -F web`：真实 PostgreSQL 19/19 通过，覆盖身份/权限/审计/恢复及模型 BFF 事务边界。日志 `web-integration.log`。
- `pnpm test:setup -F web -- --fixture teaching`：独立测试数据库准备通过。日志 `web-setup.log`。
- `pnpm test:e2e -F web`：31/31 通过，0 跳过、失败或不稳定用例，耗时 178.6 秒；包含平台接入、全部模型测试、Chat 选模与续聊、停止/撤权清屏、权限隔离、输入框交互和 375/768/1440 布局。结果 `playwright-results.json`、日志 `e2e.log`。
- `pnpm build`：7/7 任务通过，包含 Agent 分发包和 Web/Home 生产构建。日志 `build.log`。
- 模型浏览器定向复测：`pnpm test:e2e -F web -- tests/e2e/models.spec.ts` 4/4 通过，0 跳过、失败或不稳定用例，耗时 37.6 秒；结果 `playwright-models-final.json`。最后补充准备超时收尾期间的取消优先级测试，Agent 单测 86/86、PostgreSQL 14/14 与 Agent 构建再次通过。
- DeepSeek 真实调用：生产 Agent 入口连接真实供应商，两款模型各 10 项，共 20/20 completed；覆盖同步、流式、Thinking low/high/max、JSON、Schema、同步/流式工具及手工续接，逐项核对持久元记录和用量。合计 input 3927、output 435 tokens；结果 `live-deepseek-results.json`。
- DeepSeek 真实页面：管理测试 2 项、Chat 两轮上下文 4 项，共 6/6 通过；已核对配置重载、密钥不回传、页面结果和记录，复核管理与 Chat 截图。合计 input 338、output 209 tokens；结果 `live-browser-results.json`。本轮真实验收总计 26 次，input 4265、output 644 tokens。

本轮未变更数据库迁移或容器部署配置。既有容器部署和旧版本真实供应商记录保留在历史章节；本轮未重新部署五应用容器。测试使用独立数据库与 8013/3013 端口，测试服务结束后停止，保留测试数据库与本机记录，普通开发服务保持原状。

## 后续事项

- 阿里云 decision-model-preview：用户明确后续再测，仍需工作区 Base URL 与账号授权。管理入口、类型化适配及 Chat 排除规则已交付；AC-993-16/24 的阿里云真实调用保持待验，本轮不将其列为 DeepSeek 联调阻塞。

## 2026-11-04 SDK 协议复用与移除模型白名单

用户授权连续修正需求、设计、实现、回归并启动本地服务。当前需求为 PRD AC-993-34～36，当前增量设计维护在 TECH_DESIGN.v2；历史三模型范围保留为课程联调基线，生成模型的目录绑定、Runtime 与 Chat 均取消供应商和具体型号白名单。

### 本轮交付

- 目录读取 SDK 的生成模型元数据和供应商注册表，支持手工 `provider/model`；当前目录共 3,173 项。目录查询避免触发供应商 OAuth，调用时再由 SDK 执行实际认证。
- 生成调用直接复用 LiteLLM Core 的原生协议、参数映射、认证、响应和流事件归一，包括 Responses→Chat 桥接；Chat 以生成类型、平台启用/可见性及用户权限准入。
- 供应商连接参数（JSON）与 Key 加密存储，支持 Azure API 版本、AWS 凭据/区域、Vertex 原生认证等；留空保留、`{}` 清空，读取只提供配置状态。SDK 字段和执行参数边界校验阻止覆盖调用策略、客户端及消息。
- 保持 LiteLLM Core 1.106.6，增加 boto3、google-auth、azure-identity 和 mcp 2.x 依赖；锁文件已同步。该 SDK 的 Responses 路径在模块加载时依赖 MCP 2.x 类型，普通模型调用无需部署代理服务或启用 MCP 工具业务。
- 原生工具调用使用公开不透明 ID，SDK 原始工具 ID、Gemini 签名和私有消息保留在加密续接中，工具结果续接时恢复给 SDK；同步与流式均已验证。
- 保留 DeepSeek 锁定 SDK 版本的兼容守卫；其他生成模型使用 SDK 原生客户端与连接池。一次执行只调用一次 SDK 推理入口，关闭推理重试和 fallback，SDK 所需认证交互由 SDK 管理。原生非流式响应由 SDK 缓冲，应用检查归一后结果与续接大小。
- 添加窗口支持完整生成模型目录、手工标识、可选 Base URL/Key、原生连接参数与全生成模型 Chat 开关；修复选模后 Enter 意外提交，补齐手工输入的可访问标签。需求、设计正文、架构/UML/时序图、V2/V3 HTML 和运行 README 已同步。

### 当前验证结果

验证日志与私有运行材料位于忽略目录 `work/993/sdk-protocols/`；文档只保存可复现命令和结果，不保存密钥。

| 检查 | 本次结果 |
| --- | --- |
| `pnpm check` | 19/19 任务通过；包括契约漂移、格式、边界、lint、类型与全部 workspace 单测；Agent 128/128，Web 43/43 |
| `pnpm build` | 7/7 任务通过，全部应用构建成功 |
| `pnpm test:integration -F agent` | 独立 PostgreSQL 14/14，无跳过 |
| `pnpm test:integration -F web` | 独立 PostgreSQL 19/19，无跳过 |
| `pnpm test:e2e -F web` | 32/32 成功，无跳过、失败或不稳定用例，耗时约 3.5 分钟 |
| SDK 协议定向回归 | `tests/test_sdk_protocols.py` 42/42；实际 LiteLLM 转换覆盖 Anthropic/Gemini 同步、流式、工具续接与认证失败，Azure 原生版本/端点、Bedrock SigV4、OpenAI 固定及自动 Responses 桥接；上游 HTTP 为受控响应 |
| DeepSeek 真实调用 | 最终两款模型各同步/流式一次，共 4/4 completed；本轮另有前期 4 次真实回归，累计 8 次，输入 56 / 输出 8 tokens |
| 本机服务与链路 | Web 3000、Agent 8000 重新启动，数据库保留；`pnpm smoke --apps web,agent` 通过；七个供应商目录接口及已有配置读取均 200 |
| 设计阅读版 | V2/V3 各六张 Mermaid 图成功生成；375/768/1440 宽度离线检查无页面溢出、失效锚点或脚本错误 |

首轮浏览器回归暴露的 Enter 提交与标签定位问题已修复，模型定向回归 5/5 通过。之后一次全量复验被本机开发启动脚本清理同目录测试进程打断；已保留日志，最终按顺序完成集成、测试数据初始化、独立全量浏览器回归，取得上表 32/32 结果。手测服务在最后一轮回归期间保持运行。

数据库 owner 仍为 Agent/main，迁移继续 0002，Web/main 继续 0003；没有 DDL、历史 SQL 或模型数据重置。旧密文读取兼容，原加密密钥继续保留。没有提交或推送。

新增供应商的协议回归使用实际 SDK 与受控 HTTP；真实供应商账号和可用额度由平台按接入配置提供。本轮真实供应商调用覆盖已有两款 DeepSeek；阿里云决策模型真实测试继续后置。Feature Phase 保持 draft。


## 2026-11-05 System One 通用决策适配实施与验收

连续授权：将决策适配统一为 SystemOneDecisionAdapter，同时支持阿里云与 JEV 官方服务，去掉模型名称硬编码，同步需求/设计并回归、启动手测服务。需求维护于 PRD AC-993-37～39，设计维护于 TECH_DESIGN.v2 的 System One 增量章节；本文保留此前各批次历史结果。

### 实现与兼容

- `llm/systemone_decision.py` 替代阿里云专用模块，注册键为 `systemone_decision`。共用单次 POST、Bearer 认证、Choice/Noul/Score 校验及 Runtime 期限、取消、资源与元记录机制。
- 目录提供阿里云和 JEV 预设；手工命名空间为 `aliyun/`、`typesafe/`、`systemone/`，API 模型名可独立修改，移除调用和管理中的具体型号枚举。决策预设来源标为 systemone，独立于账号真实调用状态。
- 决策支持结构化指令及判据，读取输入和输出 Token 数；缺失用量保持 None。表单对决策要求 Base URL 和 Bearer Key，隐藏 LiteLLM 连接参数、禁用 Chat；服务端同步拒绝无效组合。结构化等级描述以 JSON 展示。
- Agent/main 追加 0003，迁移旧 adapter 行及 CHECK，Provider ID、版本、密文、端点、配置及历史不变；0001/0002 历史迁移与 0001→0002 SQL 字节保持。受管迁移工具登记旧/新版本检查，生成 bootstrap、0001→0003 和 0002→0003 SQL。Web/main 版本不变。readiness 要求 Agent 0003。
- PRD、BRIEF、上下文索引、技术/代码设计、两份 HTML 阅读版、运行/数据库 README 和配置示例已同步。图片/Box/实验头暂未开放，决策继续仅管理端测试。

### 本轮验证

| 检查 | 实际结果 |
| --- | --- |
| workspace 综合检查 | `pnpm check` 19 个任务通过，Agent 154 项、Web 54 项，含契约漂移、lint、类型和格式检查 |
| 构建 | 全 workspace 7 个任务通过；最终 Agent/Web 定向构建 3 个任务通过 |
| Agent PostgreSQL 集成 | 17 项通过，含空库原生/SQL、0001/0002 升级、旧决策绑定与密钥/历史保留、约束、权限、备份恢复及升级后调用 |
| Web PostgreSQL 集成 | 22 项通过 |
| System One 回归 | 新增 25 项配置/协议/错误/取消/期限/大小边界测试，包含三种命名空间、自定义版本、结构化指令、用量与 Chat 排除 |
| 浏览器 | 完整流程中的其余 28 项通过；最终模型专项 6/6 通过，包含原 LLM、阿里云三题型、JEV 预设、自定义型号、编辑保留密钥、手工型号、用量展示与 Chat 边界，共覆盖 34 个不同场景 |
| SQL 与历史 | `pnpm db:sql:check -F agent` 通过；0001→0002 SQL SHA-256 仍为 471825e3af6e2d456e1973e62bd3545a5fc47fa4a84a6e599df86173753772ea |
| 本机迁移与 seed | 已备份配置与 Agent 库，再迁移 0002→0003；原两条 LLM Provider 保留，连续两次 seed 成功 |
| 本机链路 | Web healthz、Agent readyz 返回 200，`pnpm smoke` 通过 |

回归使用真实配置持久化、Runtime 与受控 HTTP；阿里云和 JEV 真实账号调用待提供各自凭据后手测。JEV 实验能力不计入本次基础接入验收。首次浏览器运行受共享测试夹具重置影响，改为先完成集成再重新初始化教学账号、独立执行浏览器测试；新增 JEV 用例遗漏 state 的测试输入已补齐，最终模型专项无跳过、无重试通过。

本轮日志、测试截图、数据库备份及私密环境副本仅保存在 `work/993/systemone*`，不提交。手测使用正常 `compass_agent.main`，受控测试宿主使用独立端口和专用测试库。JEV origin 已加入本机允许列表。服务入口为 `http://127.0.0.1:3000/platform/models`，Agent 为 `http://127.0.0.1:8000`。Feature Phase 保持 draft；本轮不提交或推送 Git。

收尾复验时工作区会话模块及其测试发生并行变更，新增两类 TypeScript 报错。保留现有生命周期变更，通过 `isRetired()` 读取异步后的状态，并为测试参数添加 literal tuple 类型；综合检查已重新通过，另有旧页响应、SPA 历史撤权、退休页面三项浏览器用例通过。


## 2026-11-05 决策示例与原始响应

按用户要求补齐 JEV 手测材料并增加结果检查入口，对应 PRD AC-993-40。

- 决策测试页默认填充“意图分流 · Choice”，另提供“是否升级处理 · Noul”“问题严重度 · Score”“工单综合决策 · 多问题”四个可编辑示例；共用于 JEV 与阿里云。选择后点击“加载示例”，替换前保护已有输入/结果，不自动调用上游。
- 输出区增加“原始响应”标签。System One 原样保留解析后的供应商 JSON，包含 model、answers、usage 和扩展字段；仅随测试返回、存于页面内存，不写数据库。生成模型展示测试接口 JSON，流式展示汇总结果并标明来源。新调用/加载/撤权继续清理旧结果。HTTP 公开 DTO、生成类型及 SSE final 的字段排除同步调整。
- 合并后的公开响应仍受 2 MiB 上限约束，超限时清空原始响应；新增用例覆盖该边界。没有数据库迁移变化。
- 综合检查 `pnpm check`：19 个任务通过，Agent 155 项、Web 55 项。Agent PostgreSQL 集成 17 项、Web PostgreSQL 集成 22 项通过；构建全部 7 个任务通过。
- 浏览器模型回归原有 7 项通过，新增 JEV 旅程修正示例控件可访问名称后单独通过；共验证 8 个不同场景。JEV 旅程实际逐项加载并运行全部四种示例，检查加载无出站、各题型请求、原始响应与上游一致、用量、编辑保留密钥。截图已查看，日志位于 `work/993/decision-examples-*`，页面截图 `work/993/systemone/jev-examples-raw.png`。

真实供应商调用仍由用户手测使用已有账号执行；自动回归使用受控上游。运行服务沿用本机 Web 3000 / Agent 8000，当前 Feature Phase 保持 draft。
