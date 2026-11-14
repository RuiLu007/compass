# Compass 第 18 课实施上下文｜M4

## 前置能力

第 17 课 M3 的工作流和第 15–16 课模型、观察结果与预算契约。

## 本课要交付的运行能力

- 建立最小 Tool Registry、统一 Tool Dispatcher 与 Tool Execution Result。校验参数，限制并行与结果大小，记录成功、错误分类、超时、取消及 Tool Execution Event。
- 可信只读函数在进程内执行。SQL 通过只读查询接口与项目的受限零售身份查询，校验权限与范围。超时和取消须停止或可验证地终止实际执行。
- Python 在独立容器或远程 Sandbox 真实执行，约束 CPU、内存、时间、文件系统、网络与输出。管理执行环境、临时文件、Artifact 和终止后的清理。执行产物回到分析流程。
- 有副作用动作提供 Action Adapter 与审批挂钩。展示 SQL 查经营数据、隔离 Python 计算及绘图，并演示超时、代码异常、资源限制和主动取消。交付 M4、Sandbox Adapter 与执行环境选型 ADR。

## 课次边界

交付可运行的最小隔离环境。企业 Sandbox 集群、完整工具授权与 Action Runtime 治理留给后续篇章。跨进程人审由 M5 接入。

## Compass 接入约束

- SQL 沿用 docs/data-seeding.md 的受限零售连接和租户、区域约束。应用数据库、迁移或装载凭据不能作为工具连接。
- Sandbox 的分析环境不得获得宿主或运行时密钥。Artifact 只对允许访问当前任务的使用方可见，验收使用合成零售数据。

## 跨课契约

任务以 Run 进入，外部能力经受控接口调用，关键状态变化生成结构化事件。课堂复用同一个 Compass 经营分析案例，每课以可观察的正常和异常结果验收本期增量。

## 2026-11-11 设计修订

当前执行层采用独立的执行绑定与执行策略。Retail 查询归入进程内内置工具，审批要求在实际派发前判断。前述素材中的 Adapter 分类由第 18 课当前技术方案承接并修订。

## 2026-11-11 Sandbox 与模块边界修订

用户确定 OpenSandbox + NFS-Ganesha，用本地 Docker 模拟独立分配、NAS 文件保留与重挂载；并要求本课方案按 api/runtime/harness/workflows/tools 模块化组织。当前有效方案将 MCP 和 Sandbox 收进 tools 子模块，列出旧实现迁移映射；原始素材保留。验收补充见 PRD AC-13，部署、类型与生命周期见当前 TECH_DESIGN。
