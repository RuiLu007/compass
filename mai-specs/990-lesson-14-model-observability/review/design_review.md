# Feature 990 技术方案评审

> 更新：2026-11-06 | 方法：按 my-review-design 审查 PRD、技术方案、代码设计与当前源码。评审建议由主 Agent 产生；无独立人工批准或实施验收声明。

## 初审结论

**需要修改。** 总体 B 类轻量接入与用户确定的 Docker Langfuse 相符，独立栈、无业务库迁移和单一执行所有权的取舍合理。初稿存在以下具体缺口，修复后再复核。Review 范围覆盖 AC-990-01～14 的设计映射，不包含真实部署或应用实现验收。

| ID / 优先级    | 定位与触发                                                                                                                                            | 影响                                                                            | 修正要求                                                                                                                                                                                            |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| DR-990-01 / P1 | TECH_DESIGN §2.2/2.3、CODE_DESIGN privacy/mapping：仅发 compass.*；Langfuse OTel 官方映射说明该类任意属性进入 metadata.attributes，不是顶层可筛选字段 | call_id、stage、model、status 的检索与比较无法按设计直接筛选，影响 Showcase 1/4 | 在单一映射层将有限业务键转换为 `langfuse.observation.metadata.*`；根关联字段另映射 `trace.metadata.*`；代码与允许集合一致                                                                           |
| DR-990-02 / P1 | CODE_DESIGN observed_stage 取消分支调用 complete(canceled)，正文同时要求 `_finish` 失败优先；缺少状态冻结分支                                         | record_write_failed 可能被外层取消覆盖，违背原 ModelService 的实际最终状态      | 展开 final_state 的单次赋值与 complete_if_unset；ModelService finally 收尾后发布最终安全状态，外围只能补缺                                                                                          |
| DR-990-03 / P1 | TECH_DESIGN §2.3/5：主模型成功率含 canceled/零 Attempt，原生图表可能无法给出该分母；当前只允许离线脚本核对                                            | PRD FR-990-06 的实际可见比较闭环依赖不明确的平台功能，金标脚本不能替代产品展示  | 保留 Langfuse Trace/图表；补一个固定查询、只读、管理员范围的对比表，从公开 Metrics API 计算定义好的比例和未知数。若原生保存视图完全覆盖可直接复用；默认设计须有确定路径，不能降成 generation 成功率 |
| DR-990-04 / P1 | CODE_DESIGN logical.bind_provider 在 prepare 成功后，过载前准备失败无法关联已选模型                                                                   | 模型维度失败分母漏样本                                                          | 选择已知后先绑定 provider_id/model 安全快照，再 prepare；只有完全未选中模型的根失败不进入模型分母                                                                                                   |
| DR-990-05 / P2 | TECH_DESIGN §2.4 与 mapping 的未知用量行为不同：主文仍允许自动平台估价，代码仅完整时填平台 model 字段                                                 | 学员阅读和实施者获得不同的缺字段估价规则                                        | 统一为缺用量只保留业务模型 metadata，不填平台 model/cost；完整用量才允许平台估价，金额缺失仍未知                                                                                                    |

## 核验依据

- 当前 `routing/service.py` 的 JEV/Flash 与 inference 阶段、`llm/executor.py` 的两个 Attempt、`models/service.py` 的 finally/_finish 和 record_write_failed 优先级。
- Langfuse v4.75.0 官方 Compose：六服务依赖、API/Worker 双角色与独立存储；用户确认在 Docker 本机部署。
- Langfuse 官方 OTel 文档的 Metadata Mapping 与顶层过滤要求；Metrics v2 的 observations 视图及元信息筛选。
- OTel Python 1.45.2 BSP 源码：HTTP 超时由 exporter 控制，BSP export_timeout 未生效，shutdown 有独立等待预算；设计已有说明。
- 本次受控 LiteLLM 1.106.6 探索：MockTransport 模拟两个并发非流式模型请求，动态异步函数回调分别命中一次，全局 callbacks 不变。直接传 CustomLogger 对象的探索未命中，不采用。failure/stream/late callback 未验证。

## 保持与延后

保持：用户已确认的 Langfuse、OTel 标准、进程内有界导出、正文不采集、平台管理员边界、无新业务库迁移。

延后：自动评估/Prompt 管理、Collector、持久计量、租户自助观测、HA 和自动告警值班。它们不进入本次修正条件。

## 修订与复核结论

**通过（设计层面）。** 五项发现已落实至需求、主方案与代码设计，当前没有待负责人决定的范围冲突或未修复的 P0/P1。通过只覆盖方案可实施性与契约一致性，人工审批与实施验收保持独立；Feature Phase 仍为 draft。

| 发现      | 修订与复核                                                                                                                                                                                                               |
| --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| DR-990-01 | CODE_DESIGN metadata.py 展开白名单键→顶层 metadata 的替换，根补 trace.metadata.call_id；主方案 §2.2 与查询约定一致。纯函数检查已验证 stage/call_id 可筛选键形状及非法键拒绝                                              |
| DR-990-02 | ScopeTerminal.final_state 为 ModelService 收尾权威；外围 complete_if_unset 只补缺；end_once 幂等。设计片段反例验证 record_write_failed 不被 canceled 覆盖，end 调用一次；收尾失败还提升到请求根，根收到取消也保留 failed |
| DR-990-03 | PRD FR-990-06/12、线框与 TECH_DESIGN §2.7 明确固定只读对比表；最多两模型/三时间窗，从 Langfuse Metrics v2 固定查询计算。无本地统计库与通用查询器；零分母及部分失败的展示已展开                                           |
| DR-990-04 | logical 在 prepare 前绑定已选择 Provider UUID/model，再补配置快照；未选到模型的失败只归整轮，模型分母口径一致                                                                                                            |
| DR-990-05 | 统一为锁定 SDK 的明确估价；unknown 不填平台原生 model/cost，保留 metadata.model 用于筛选。反例验证缺用量时无原生 model/cost、reasoning 不重复进 total                                                                    |

### 需求到验证的复核

| AC    | 设计与任务                     | 实施时必须执行的验证                                     |
| ----- | ------------------------------ | -------------------------------------------------------- |
| 01    | §2.9，T6                       | 实际 Docker 六服务、初始化、保留卷重启                   |
| 02/03 | §2.1/2.5，T1/T3                | 分类/主推理/诊断/决策的真实层级与 Attempt 计数           |
| 04    | §2.3，T3                       | 单调时钟夹具、1 秒→1000 ms、首片段与非流式空值           |
| 05/06 | §2.1、CODE_DESIGN terminal，T3 | 受控429/取消/断流/零Attempt/记录收尾失败                 |
| 07/08 | §2.4/2.7、summary.py，T2/T5/T7 | 两模型固定金标、成功率/未知消耗/费用与平台公开查询一致   |
| 09/14 | §2.6，T2/T6                    | 后端超时/401/队列满/关闭预算与模型路径隔离               |
| 10    | §2.2、privacy/metadata，T1/T2  | 日志、OTLP、后台三出口哨兵扫描                           |
| 11/12 | §2.7，T4/T5                    | BFF+Agent 平台权限、响应头、取消后编号、旧端与采样       |
| 13    | §2.8、§4，T4/T6                | 无新业务迁移，旧记录保留、契约生成与实际 PostgreSQL 回归 |

### 已执行的设计级检查

- 读取当前实际入口和固定版官方依赖源；LiteLLM 动态函数 success_callback 受控并发探索通过，全局列表保持不变。该探索不覆盖 failure/stream/迟到场景。
- 所有 Python 设计片段语法检查；执行安全错误归一、禁止 input 属性、超长字段拒绝、顶层 metadata 映射、late receipt 冻结、unknown 不填 model/cost、零分母、部分费用未知和最终状态优先级的纯函数反例，均通过。这些检查只覆盖设计骨架。
- Markdown 格式、相对链接、Feature 路径检查通过；7 张 Mermaid/SVG 图已在阅读版构建期渲染。HTML 离线、搜索、代码复制/展开、图表缩放/SVG导出、锚点、打印与 375/768/844/1440px 布局检查通过，桌面和窄屏目标表、密集图、长代码已查看；未替代应用验收。

### 实施门槛与未执行项

T2/T6 开始前完成最小接入核验：固定版 Langfuse 的 OTLP metadata/usage/cost/TTFT 映射、Metrics v2 固定查询及 SDK 回调 failure/stream/late 行为；解析镜像 digest，确定本机资源并记录。它们属于已经列入任务的实施验证，不自动扩张范围。本轮未启动 Docker、未安装 OTel 应用依赖、未调用真实供应商、未执行本 Feature 的权限或浏览器应用验收。

学员 HTML 是设计阅读呈现，不能将上述实施门槛改写成已通过；由用户指定的 subAgent 生成并执行离线阅读行为检查。

## 学员阅读版交付检查

用户要求的 subAgent 已使用 my-design-to-html 生成 `design/TECH_DESIGN.html`，编辑源为 `design/document/guide.md` 与 `edition.json`；11 处核心代码从 CODE_DESIGN 唯一块提取，最终产物 7 图、15 表。重建命令在 design/README.md。

本次检查报告与截图留在 `work/lesson-14-design/html-qa/`（report.json / visual-report.json / scroll-report.json），不进入阅读正文或提交范围。浏览器离线网络请求数 0、脚本错误数 0、重复锚点数 0；窄屏允许表格/图/代码在各自容器滚动，页面无横向溢出。源方案状态、根失败优先级、费用未知语义和 Provider+当前模型筛选已同步。

## 分布式增量复核

2026-11-06 用户明确要求补齐实例标识、可信内部上下文传播、跨实例采样一致性并实施。已核对追加设计：上下文只在原服务鉴权及动作授权后提取；BFF 不接受浏览器上下文，Provider 不传播；ParentBased 与显式句柄保持一致；实例指标明确局部作用域。设计增量通过，新增 AC-990-15～18 随实施验证。无持久 Worker、Collector 或全局配额扩张。
