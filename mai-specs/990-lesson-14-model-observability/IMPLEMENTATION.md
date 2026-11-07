# Feature 990 实施与验收

2026-11-06。用户授权补齐分布式观测设计，并完整实现和验收。有效需求为 [PRD](spec/PRD.md) AC-990-01～18，实施收敛见 [TECH_DESIGN](design/TECH_DESIGN.md)。本期实现与验收完成。

## 交付

| 任务                       | 状态     | 结果                                                                                     |
| -------------------------- | -------- | ---------------------------------------------------------------------------------------- |
| T1 观测句柄 / 分布式上下文 | complete | 独立 OTel Provider、显式父子关系、唯一实例身份、W3C carrier、远程采样继承                |
| T2 出口 / 自诊断 / 用量    | complete | 白名单、原生 tracestate 清理、512/64 有界 BSP、请求级成本回执、未知用量/费用与 JSON 诊断 |
| T3 执行链集成              | complete | 请求→分类→路由→逻辑调用→排队/退避/Attempt→收尾；不增加业务 DB 读取                       |
| T4 BFF / DTO / 权限        | complete | 生成契约、安全响应头、取消/错误关联、平台管理双侧鉴权、浏览器伪造头隔离                  |
| T5 UI                      | complete | 调用编号复制、本实例导出状态、固定窗口两模型比较、旧结果提示、窄屏布局                   |
| T6 Docker / 运行入口       | complete | Langfuse v4.75.0 六服务，固定 digest、私有初始化、重复启动与停机保留数据                 |
| T7 验证与自检              | complete | 全量 check/build、真实 PG / Langfuse / 浏览器 / 容器 / 默认端口 smoke、代码自检          |

## 本次执行结果

| 检查                           | 结果                                                                                                                    |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| `pnpm check`                   | 19 个任务通过；工具 59、Agent 214、Web 61、Home 19、数据工具 34、Memory 1、Worker 2；含格式、lint、类型、契约和依赖边界 |
| `pnpm build`                   | 7 个任务通过，含五应用、数据工具和生成契约                                                                              |
| Agent 观测单元                 | 18 通过，覆盖重试、零 Attempt 过载、SystemOne 用量、首片段/排队、成本未知、收尾失败、队列与退出、OTLP 原生字段哨兵      |
| `observability_acceptance.py`  | 6 通过：真实 Langfuse + PG，双进程金标/消息 carrier、双 Agent HTTP/采样、Chat 重试、401/503/慢导出故障                  |
| PostgreSQL 回归                | Agent 模型 7，数据库迁移/升级/权限/恢复 14；Web 22，通过专用测试库执行                                                  |
| 浏览器 `observability.spec.ts` | 1 完整旅程通过：完成/停止后复制、平台对比、租户/匿名拒绝、375px 与 1440px 截图                                          |
| Langfuse 浏览器                | 初始化账号登录、项目 Tracing 页面和入库观测可见；截图确认 input/output 为空                                             |
| Langfuse Docker                | 六服务健康；重复启动成功；整栈停止时模型调用 completed（本次受控调用约 21 ms），实例报导出失败；重启后原 214 条记录保留 |
| Agent / Web 生产镜像           | 两镜像构建成功；独立验收库和 18000/13000 端口，Web→BFF→Agent→PG smoke、平台观测 API、容器→Langfuse OTLP 通过            |
| 本机默认端口                   | `.env` 接入本地 Langfuse，Agent 8000 / Web 3000 ready，`pnpm smoke` 通过                                                |
| 学员 HTML                      | 8 图、17 表、20 代码块；离线交互、四种宽度、截图、链接与代码语法检查通过                                                |
| 文档与自检                     | 本次格式、内部链接/manifest、`git diff --check`；代码自检见 [报告](review/code_review.md)                               |

上游 SDK 的 TypedDict ReadOnly 警告仍存在，相关测试全部通过。

## AC 对照

| AC  | 本次验证                                                                                                          |
| --- | ----------------------------------------------------------------------------------------------------------------- |
| 01  | Docker 首启、账号/项目初始化、重复 up、整栈 down/up 与持久化查询                                                  |
| 02  | 真实 Chat Trace：分类、路由、主推理 Logical Call、实际尝试和版本字段                                              |
| 03  | Provider 测试与 SystemOne 受控调用，单 Attempt、类型化用量，无正文采集                                            |
| 04  | 排队与 20 ms 首正文夹具；Thinking 不计首字、非流式无首字值；真实延迟金标约 1000 ms                                |
| 05  | Chat 首次 429 后成功：一个推理 Logical Call、两个 Attempt、一次退避，ERROR/DEFAULT 正确                           |
| 06  | 取消/断流、准备失败、零尝试过载与收尾写失败；根状态与业务一致，停止后保留编号                                     |
| 07  | 两进程/两模型金标，每模型四终态各 2、逻辑 8、Attempt 8、已知 input/output=24/16、已知 USD=0.02、未知用量/费用各 6 |
| 08  | 无价格/无用量/不完整缓存类别保持未知；已知零价可表达；Reasoning 不再累计到总用量                                  |
| 09  | 真实 HTTP 401/503/慢后端、整栈停止与队列满；业务调用成功，诊断计数可见                                            |
| 10  | 输入/状态/错误/头/续接/思考边界及本轮哨兵；OTLP protobuf、Langfuse 查询和日志出口检查                             |
| 11  | Agent / BFF 权限测试，浏览器租户/匿名拒绝；响应无 Key 和内部地址                                                  |
| 12  | 采样/未采样、禁用协议回归、停止保留编号、无 final 不提交；BFF body 超时保留关联                                   |
| 13  | 无本期业务 SQL / 迁移新增；原 PG 配置/记录/权限回归和 contracts 检查                                              |
| 14  | 积压/丢弃条件下独立 Observer 关闭有界，重复关闭幂等；测试 Agent 真实信号退出                                      |
| 15  | 两个独立进程向同项目导出，instance_id 不同；跨实例金标无重复，状态明确本实例                                      |
| 16  | 可信 HTTP 与跨进程消息 carrier：同 trace、不同 span、正确 parent；浏览器/无鉴权输入隔离                           |
| 17  | 父采样与下游相反 ratio、非法 carrier，继承与新根规则正确                                                          |
| 18  | 实例队列溢出/失败计数隔离，整栈停止时本实例报错；跨实例报表说明样本口径                                           |

## 本机配置与前置版本对齐

开发入口为 <http://127.0.0.1:3000>，Langfuse 为 <http://127.0.0.1:3002>。配置已注入忽略的根 `.env`，原文件保存在私有 `work/observability/compass-env-before-observability`。初始化密码和项目 Key 留在私有文件，[运行说明](../../deploy/observability/README.md)登记读取位置、启停、格式和存储。

接入本机时发现日常 Agent 库为 0003。已先保存私有 `agent-before-0004.dump`，再执行第 13 课已交付的 0003→0004 迁移。迁移前后 model_call=34、model_change=3、model_provider=3，数量一致；重复 seed 两次与默认 smoke 通过。这是前置版本对齐，本 Feature 没有新增业务迁移。原零售库与其他 schema 保留。

## 材料与范围

执行日志、机器结果和截图位于忽略的 `work/lesson-14-*`、`work/observability/`、`work/990-*.png`、`work/lesson-14-design/html-qa/`。私有环境文件 0600，未加入 Git；依赖和构建目录保持忽略。

供应商 HTTP 使用真实 SDK 与受控夹具，未向真实厂商发送验收调用。消息链路验证 carrier 与父子/采样语义；Worker 业务消费、生产 HA、告警/SLO、正式计量和强审计沿需求边界后续建设。
