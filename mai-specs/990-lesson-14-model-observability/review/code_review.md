# Feature 990 代码自检

2026-11-06，Profile standard。按 my-review-code 执行主任务自检，范围为本 Feature 的 Agent / Web 观测、生成契约、依赖、Docker 与运行入口。

## 结论与范围

实现与 AC-990-01～18 一致，发现的问题已修复并复验，没有未关闭的 P0/P1，建议合并本 Feature 范围。本报告覆盖主任务自检，未登记独立审查人。

当前 HEAD 为 `754aaf67804e8a81d9ebb3b4090374ae244fc7b2`，已检查 staged / unstaged / untracked，本范围没有 staged 文件。实施源码及配置共 48 个变更文件，按名称排序拼接名称/内容的 SHA-256 为 `dcb01311fa33cb51d5dcd4ad697e0cb614206f0aab954982be623ea632e716a9`；排除文档、截图和私有 work 数据，包含新增文件与生成契约。课程仓库其他改动、其他 Feature 和既有 AI 资产保持原状。

## 已修复发现

| 优先级 | 问题与影响                                                 | 修复与复验                                                                       |
| ------ | ---------------------------------------------------------- | -------------------------------------------------------------------------------- |
| P1     | prepare 外的额外 Provider 读取会扩大准备预算并新增失败路径 | 复用原 prepare 快照；测试禁止额外 repository.get，业务仍完成                     |
| P1     | 可选 SDK 流组装/估价异常可能改变模型结果                   | 回调、组装和估价隔离；注入异常后调用成功                                         |
| P1     | latency 秒/毫秒转换、未区分分类 Attempt 会放大统计         | 使用原生毫秒和 inference 条件；两个进程 1 秒金标验证次数、状态、Token 和未知费用 |
| P1     | BFF body 超时重建错误会丢掉已收到的关联头                  | 安全错误归一后附关联信息；虚拟时钟验证 190 秒期限                                |
| P1     | 原生 OTel tracestate 可绕过属性白名单                      | 内部保留 carrier，出口重建安全 SpanContext；protobuf 哨兵验证清除 vendor 文本    |
| P1     | 路由失败/等待取消可能缺少结束 span 或错误阶段状态          | 路由上下文覆盖选择和原快照读取；wait/backoff 显式记录取消/到期                   |
| P1     | Turbo 未允许项目 URL / 观测运行变量，页面会缺配置          | 补运行白名单；浏览器、生产容器 BFF 与默认端口验证                                |
| P2     | 小 uptime 下 started+180 的浮点舍入可能超出原上限          | 最大 deadline 向起点舍入；保持 180 秒上限，214 项 Agent 测试通过                 |

## 契约核对

- 服务鉴权与动作授权后建立根；BFF 丢弃浏览器 carrier。可信 HTTP / 消息通过显式父上下文传播，ParentBased 跟随远程采样，Provider 不接收内部 carrier。
- ModelService 拥有逻辑终态；record_write_failed 提升到根并优先于外层取消。观测不触发模型重试、切模或业务库读取。
- 实际进入 Adapter 的 Attempt 才形成 generation；父级不重复填原生用量/费用。reasoning、未知用量、缺价格、不完整缓存字段和四种终态的口径已有测试。
- 元信息从白名单构造，无自动异常采集；出口检查 scope、属性和事件，并清除原生 tracestate。OTLP、日志和 Langfuse 查询已检查敏感哨兵。
- 单 BSP 有界导出，失败、溢出与关闭不进入模型控制分支。本实例诊断与跨实例报表清晰分开。
- Compose 六服务固定 digest，独立凭据与卷；Web env 文件只有固定公开项目 URL。本期无业务迁移新增。

本次全量 check/build、真实 PostgreSQL、Langfuse、浏览器和生产镜像结果见[实施记录](../IMPLEMENTATION.md)。供应商 HTTP 使用真实 SDK 与受控夹具；Worker 业务消费、生产 HA、告警/SLO、正式计费和强审计沿原需求边界后续建设。
