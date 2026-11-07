# 993 代码评审

日期：2026-11-03。依据本项目 my-review-code；有效需求为 [PRD](../spec/PRD.md)，方案为 [TECH_DESIGN](../design/TECH_DESIGN.md)及 [CODE_DESIGN](../design/CODE_DESIGN.md)。实施和测试结果持续归集至 [IMPLEMENTATION](../IMPLEMENTATION.md)。

## 结论

Runtime 实现阶段确认的 12 项 P1、4 项 P2 均已修复；数据库、Agent Runtime、BFF 和 UI 的定向复核通过。未发现超出第 11 课 MVP 的实现。2026-11-03 两款 DeepSeek 真实 Runtime 20/20、管理与 Chat 页面 6/6 通过；真实联调发现的 A05/A06 已修复并独立复评。当前 DeepSeek 交付切片通过代码评审。后续分析输入框改版另发现 2 项交互问题，均已修复并独立复核；详见下文补充审查。阿里云决策模型按用户安排后测，其真实验收保留后续事项，Feature 整体仍待该项验证。

保持以下范围：平台统一 Provider，租户显式授权调用，两款 DeepSeek 进入 Chat，决策模型仅管理端测试；Agent 内置薄 Runtime、三张必要业务表、单次上游 HTTP。未扩展代理平台、自动路由、重试/Fallback、预算、Agent Loop、SQL 工具执行、Memory 或 Worker 业务。

## 审查范围与基线

HEAD：`0972e2959095cb22b72b212beba356e020ccdffc`。工作区包含大量既有改动，相关新源码多数未跟踪；分别检查 staged、unstaged 和 untracked，采用当前文件审查，未将全部仓库差异归因于本需求。未提交、推送或改动无关历史资产。

主 Agent 统筹实现与验证，两个独立审查 SubAgent 分别覆盖 Web/BFF/UI + Agent Runtime，以及数据库/交付/课程边界。审查方只写报告与隔离探针，修复由实施方完成，再回交复评。原始片段与本机探针保存在忽略目录 `work/993/review-*.md`；本文件是正式评审记录。

范围包括 Agent `models/`、`llm/`、`model_api.py`、DTO、迁移和工具，Web 模型管理与 Chat、DocumentSession、BFF、权限与 Web0003，生成契约、Compose 和 CI。旧 `0001_demo_fixture.py` 与 HEAD 迁移前目录中的源逐字节一致。

## 发现与关闭记录

所有发现均属于当前文件实现，以下路径相对 Compass 根目录。首轮探针与复评命令详见后续验证记录。

| 编号 | 严重性与首轮问题                                            | 最小修复及复核                                                                                                                                                       | 状态                       |
| ---- | ----------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------- |
| W01  | P1：管理测试按钮被 enabled 限制，新建默认停用配置无法先测   | `features/models/pages.tsx` / `playground.tsx` 去掉管理测试门槛；浏览器验证默认停用保存后直接测试，Chat 保留启用校验                                                 | 已修复，浏览器通过         |
| W02  | P1：Chat 只保存 text/status，重置后丢失安全错误类别         | `analysis-composer.tsx` 保留 error_code、未知结果与调用标识；UI 与 BFF 共用安全文案；鉴权失败和截断保留在对应轮次                                                    | 已修复，浏览器通过         |
| W03  | P1：停用模型返回 model_unavailable 后仍保留旧选择，无法恢复 | `request-scope.ts` 保留安全 code；Chat 清选择、刷新列表并保留输入；新建对话也刷新                                                                                    | 已修复，浏览器通过         |
| W04  | P2：最近测试只显示状态和时间，遗漏能力与绑定版本            | 列表源码映射显示实际测试能力组合、binding_version 和过期标记                                                                                                         | 已修复，源码映射复核       |
| W05  | P1：BFF 丢失 view=module，租户摘要被 Agent 当作 Chat 拒绝   | `model-handler.ts` 透传 view；双视图可信 purpose 单测与真实租户摘要浏览器验证                                                                                        | 已修复，单元/浏览器通过    |
| W06  | P1：工具 name 分片被替换，多工具续接名称错误                | `tool-deltas.ts` 按 index 累加 name/arguments；实际 SDK 探针、交错工具单测和手工双工具回填浏览器场景通过                                                             | 已修复并独立复评           |
| A01  | P1：同步响应缺少 1 MiB 可见输出限制                         | `llm/litellm_chat.py` 统一同步/流式预算；超限结果有界失败并清正文；精确上限与超一字节测试                                                                            | 已修复并独立复评           |
| A02  | P1：SDK 对正文含 [DONE] 子串提前终结                        | `llm/transport.py` 精确完整 SSE 帧识别，合法 JSON 字面量等价转义给 SDK；真实哨兵缺失仍不完整                                                                         | 已修复并独立探针通过       |
| A03  | P2：前后端输出预算相差工具数组分隔字节                      | 两端统一 UTF-8 正文 + 各工具紧凑 JSON 字节求和，UI 同步/流式共用 helper；7 项前端协议测试通过                                                                        | 已修复并独立复评           |
| D01  | P1：未知 sequence 的库被误判为空库并接管                    | `scripts/database.py` 检测非表/索引关系，在修改 owner/marker/ACL 前拒绝；独立 PG 探针和未修改权限断言通过                                                            | 已修复并独立复评           |
| D02  | P1：缺列/类型/主键漂移仍返回 already_applied，readyz 也放行 | 0001 固定结构检查；0002 反射核对列/类型/可空/PK/FK/索引与 CHECK 存在/验证状态；版本表 PK 单列检查；readyz 精确版本和零行完整列查询。缺列、类型、CHECK、PK 负例均通过 | 已修复并独立复评           |
| D03  | P2：设计发布顺序与实际工具不同                              | 统一 Agent 0002 → Web 0003 → 两端应用，各库独立事务                                                                                                                  | 已修复                     |
| D04  | P2：Docker 上下文包含 .next-e2e 派生缓存                    | 增加 `.dockerignore` 排除，Git/格式排除同步；使用更新上下文重建镜像                                                                                                  | 已修复，容器重建与复验通过 |

### A04 / P1：数据库已提交但终态误报失败

最终 Webpack 浏览器执行出现工具续接 `record_write_failed`；同一 call 的 PostgreSQL 记录实际 completed。独立复现让事件循环暂停 2.05 秒，同时数据库线程按时提交，旧 `models/service.py` 的等待超时回调先恢复便直接返回失败。

修复：写入线程接收深拷贝的稳定结果；超时后只接受同一次已结束、未取消任务明确返回 True 的提交结果；任务未结束或抛错仍失败，全程无数据库/模型重试。写入期间取消继续向上传播。三项独立回归分别覆盖已提交结果、未完成写入、写入阶段取消，3/3 通过；Agent 全部单测 50/50 通过。修复后的工具续接浏览器定向 1/1 通过，最终完整Webpack浏览器25/25通过，0 skipped/unexpected/flaky。

### A05 / P1：压缩响应在解码前被解析

真实 DeepSeek 同步响应为 HTTP 200、Content-Encoding gzip，原观察器直接解析底层压缩字节，导致 provider_protocol_error；独立 gzip SSE 探针同样未产生有效帧。修复在协议观察前使用 HTTPX 即时解码，外层移除 Content-Encoding/Content-Length，按解压后大小校验并关闭原响应。新增 6 项回归覆盖同步/流式、内容及响应上限、损坏编码与首帧及时产出；独立普通/压缩 4 模式、首包前及流中取消复核通过。真实两模型同步/流式已通过，本项关闭。

### A06 / P1：同步工具响应附带 index 导致严格 DTO 拒绝

真实 SDK 同步工具结果含 index 元数据，直接用项目 ToolCall 校验触发 extra_forbidden。修复在 Provider 适配层提取 id/type/function.name/arguments，保留入站 DTO 严格校验和工具参数校验。新增 5 项回归覆盖 gzip 与 index 的 Thinking 工具续接，以及非法 JSON、缺字段、错误类型、未知函数；独立探针确认私密推理只回传供应商，入站 index 继续拒绝。真实两模型同步/流式工具与续接通过，本项关闭。

## 分析输入框补充审查（2026-11-03）

用户确认本轮先完成界面交互。审查覆盖 `analysis-composer.tsx`、新增 `analysis-attachments.tsx`、分析页面 CSS 及 `analysis-composer.spec.ts`。独立 SubAgent 只进行源码审查与隔离浏览器探针，修复由实现方完成。当前补充范围无未关闭的阻塞项，附件上传解析、联网搜索与服务端会话继续留待后续实现。

| 编号 | 问题 | 修复与复核 | 状态 |
| ---- | ---- | ---------- | ---- |
| U01 | 已有对话时，删除最后一个附件会清除离页提醒 | dirty 计算保留已有/正在进行的对话；移除发送前多余的清零。浏览器覆盖完成对话、添加/删除最后附件再离开页面 | 已关闭 |
| U02 | 低高度视口可能裁切发送按钮与附件说明 | 矮屏压缩输入区，dock 限高并允许收缩及内部滚动；独立 375×300/350/375/400/600 探针与实际页面 375×300/350 测试确认可触达 | 已关闭 |

新增交互测试 6/6 通过；最终 U02 修复后定向复测 1/1 通过。既有分析导航、租户隔离及 Chat 选模/停止/撤权清屏 3 项回归全部通过，使用真实 Web/BFF/Agent 与受控上游。Web lint/typecheck 及 43 项单元测试通过。375/768/1440 的新建、附件和对话截图已人工检查。新建居中与对话吸底使用页面状态，文件只留本页内存；带附件发送明确阻止。Thinking/high 按模型能力默认开启，界面不提供搜索或 Thinking 开关。无数据库或模型 HTTP 契约扩展。

原始审查、探针及截图保存在忽略目录 `work/993/composer-*`；当前实际结果汇总见[实施记录](../IMPLEMENTATION.md#2026-11-03-分析输入框交互)。

## 验收映射

| PRD 验收条件 | 当前实现与验证                                                                                                                                            | 剩余条件                                |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------- |
| AC01～09     | 平台身份独立于租户；配置持久化/密文/幂等版本；目录与公共摘要；启用/展示独立。API、真实 PostgreSQL 与管理/租户浏览器场景覆盖                               | 无新增业务决定                          |
| AC10～15     | 两款 DeepSeek 真实 Runtime 20 次、管理页面同步及 Chat 6 次通过；同步/流/用量/工具及续接/Thinking 三档/JSON 与本地 Schema 全覆盖；故障由受控样本覆盖       | 已通过                                  |
| AC16         | 决策协议输入、类型化结果、非法输入和异常响应通过受控验证                                                                                                  | 用户明确阿里云真实联调后测              |
| AC17～20     | 首包前/流中取消释放、单次出站、参数/错误/上下文隔离、统一 Runtime 与生成契约；独立探针及跨层浏览器覆盖；压缩流取消专项通过                                | 已通过当前覆盖                          |
| AC21         | Agent 保存无正文/密钥的配置与调用元记录；安全错误及关联 ID，未知用量保持 null；PG 和故障测试覆盖                                                          | 无新增条件                              |
| AC22～25     | 本页 Chat 历史、首次选择、换模新上下文、禁重复/取消/清屏；决策和其它模型不能进 Chat；models.invoke 显式授予/撤回，旧模板未回填；两款 LLM 真实两轮对话通过 | AC24 的阿里云真实管理测试后测；其余通过 |
| AC26～27     | 保持本课范围，可信 BFF、精确端点允许列表、平台/租户授权及输入覆盖拒绝；静态审查与负例通过                                                                 | 无范围扩张                              |
| AC28         | Agent 0002/Web0003 原生与 SQL、旧数据、运行角色、摘要、漂移拒绝、备份恢复及凭据解密；旧迁移源未变                                                         | 已通过                                  |
| AC29～30     | 375/768/1440 截图与键盘/焦点/导航，基础21个旅程和4个模型旅程；错误态与基座独立                                                                            | 已通过                                  |
| AC31～33 | 新建/对话两态、单行附件与 10 个上限、横向滑动、清理与发送阻止、默认 Thinking、键盘及矮屏可触达；6 项浏览器场景与最终矮屏复测通过 | 当前界面范围已通过；后台能力按用户安排后续接入 |

## 实际验证与限制

- 已通过的全局 `pnpm check` 包含57项工具测试及全部应用静态/单元检查；本轮修复后应用单元共160项，加工程工具共217项；具体分布见实施记录。
- Web PostgreSQL 19项通过。Agent正式集成先后6、11项通过；追加PK负例后的数据库9项通过，再合并3项业务集成构成最终12项。
- Agent API readiness新增6类结构/版本检查后，12项API测试通过；数据库源码校验及两端SQL再生成检查通过。
- 独立运行SDK探针：同步超限、正文含[DONE]、缺真实结束、交错工具、首包前取消、续接actor/scope/provider/binding_version/TTL错配；均达到预期。
- 完整浏览器一次25/25通过，无flaky；截图已人工查看管理、测试与Chat桌面/手机页面。独立测试宿主改用Webpack以稳定隔离缓存，最终整套25/25通过。
- 本轮真实 DeepSeek 结果经独立 SubAgent 核对：Runtime 每模型 10 个用例，20/20 完成且数据库元记录一致；页面 6/6 完成。结果、失败轮次及用量统一见实施记录；没有将这些结果计入阿里云真实验收。
- A05/A06 新增 11 项回归后 Agent 61/61，lint/typecheck 通过；修复后全局 check 19/19（应用单元160 + 工具57 = 217），Agent PostgreSQL 12/12，Agent 构建通过。
- A05/A06 后完整浏览器回归再次 25/25 通过，0 skipped/unexpected/flaky，184.4 秒；独立结果 `work/993/live-final-playwright-results.json`。该轮使用受控 Provider，真实供应商验收仍按上文 20+6 次统计。

## 复评版本指纹

以下为修复后的关键文件 SHA-256，完整可复查版本仍以当前工作树及实施记录为准。

| 文件                                                | SHA-256                                                            |
| --------------------------------------------------- | ------------------------------------------------------------------ |
| `apps/agent/src/compass_agent/llm/transport.py`     | `332520c03f5f63f8251cea93df467145863580276a124a14c02b9c6621ffc5c3` |
| `apps/agent/src/compass_agent/llm/litellm_chat.py`  | `f8e35e0805310b2b916a5667040861a4b5f98dd56f1f9cba28df0734c8a3d918` |
| `apps/agent/src/compass_agent/models/repository.py` | `f7cf69d5d75070020552247ce2b85770ee4bd6f6b48f54fec0778fa19b8554f9` |
| `apps/agent/src/compass_agent/main.py`              | `c0b8d81873f5a5c6087c702d54cfa79ddaf6810f1feb6282b18a7e7a2719ed98` |
| `apps/agent/scripts/database.py`                    | `ded4cb334a3383167bc8a6cb1be878fdb5537f555ba87e3cc57de3517b61db98` |
| `apps/web/src/server/model-agent-client.ts`         | `d02b6ad76ad5b3003375b7722546c762f7a6d3a8cf7669c3bcc154bfc12798e9` |
| `apps/web/src/server/http/model-handler.ts`         | `2f5bdf61134a162bf040d88298080bb12a56c9c5daf9e64825e93ae42da3245d` |
| `apps/web/src/components/analysis-composer.tsx`     | `17e4ca6c916d6d2a180a8e17195d06e6af161e4c52f04346b20853d05cc1fe52` |
| `apps/web/src/features/models/use-model-call.ts`    | `4962df3c352200e51b04510a9e4586fa8c11515c73b56de49f2031e0e348e109` |
| `deploy/compose/compose.yaml`                       | `20dc78ff83cc3db1476bfd8ea7a60cb835a3b2b96472e26614381472a3f0f251` |
| `apps/agent/src/compass_agent/models/service.py`    | `57f73639f86f8db675a749261b644c89b48d98f0c657fb61a9265086e3025a3e` |
