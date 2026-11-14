# 978 实施记录

状态：本次实现、验收与代码审查完成。用户于当前会话批准按技术方案实施。

## 已实现

- 默认清单收敛为 todo_write、database_query、python_analysis；个人 MCP 沿用统一 Registry 和 Dispatcher。
- 六个零售/周报/Workflow 专属工具、固定任务路由和课堂预算写入退出生产。普通案例使用同一请求与模型循环。
- 连接实体、凭据和探测迁入 data_connections，保持原表、记录与接口。PostgreSQL 查询发现授权 schema、表和字段注释，支持多连接选择；SQL 结果登记为当前 Run 的 AuthorizedInput。
- 新请求移除 showcase 与 constraints.verification；已受理旧请求通过只回读历史回执的内部入口兼容，owner、内容与幂等键继续校验。
- 历史工作流保留查看、改名、启停和导出，旧执行契约返回 workflow_unavailable。发布前的只读检查列出仍依赖退休能力的非终态 Run。
- 教学历史实现移入 examples/showcases/retail/reference；独立 runner 保留 Skill 计算演示。AGENTS 正文加入目录、注册、演示准入、依赖边界和退出约束。

## 验收进展

| 检查                                         | 本次结果                                                                                                               |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Agent 单元测试                               | 341 passed；包含实际 wheel 构建与工具目录边界                                                                          |
| Web 单元测试                                 | 148 passed                                                                                                             |
| 普通 PostgreSQL / Sandbox / 审批恢复专项     | 7 passed；使用独立数据库、RLS 只读账号、真实 Sandbox / NFS 和 Redis                                                    |
| 真实模型自主查数                             | 3 passed；DeepSeek V4 Pro 自行发现表和字段，2026-04 WEB 合计 350 分、2026-05 STORE 合计 700 分，2027-01 明确说明缺数据 |
| 浏览器专项                                   | 3 passed；桌面与 390px 窄屏、键盘发送、无案例字段、历史工作流查看与导出                                                |
| Agent / Web / Worker / Memory lint、类型检查 | 已通过；最后改动后继续复验                                                                                             |
| Agent / Web / Worker / Memory 构建           | 已通过                                                                                                                 |
| 契约生成与漂移                               | 已通过                                                                                                                 |
| 数据库 SQL 派生一致性                        | 已通过；本次无 DDL                                                                                                     |
| 工具入口测试                                 | 60 passed、13 skipped，0 failed                                                                                        |
| Compose 配置                                 | config --quiet 通过；未执行部署                                                                                        |
| 活动 Run 切换检查                            | 当前本机 ready=true，受影响 Run 为空                                                                                   |

扩展回归累计覆盖 67 个用例，65 个已通过，2 个需要主动重启 Sandbox 控制服务与 NAS 的用例未启用。首轮扩展测试的专用宿主数据库未供应；供应隔离测试库后复验发现并修复 Checkpoint 通用状态字段遗漏、旧夹具凭据依赖及过期约束断言，失败项均已定点复验通过。物理 SQL 审计位于实际执行层，持久重放只执行一次查询。

[代码审查](review/code_review.md) 已完成范围核对与问题复评。

## 验证边界

真实模型验收只约束数据结果与缺口说明，未限定调用序列。数据表注释标明金额单位与汇总粒度，通用提示词提醒区分无记录、NULL、零值及业务笔数。历史 Retail 账号额外身份核验保留在 data_connections/legacy.py，普通连接无此依赖。

旧案例绑定的测试迁至参考目录，核心测试改为通用 SQL、自定义工具和持久行为，新增无 Retail 配置的真实链路。reference 源码仅作历史阅读，保留退出时的接口引用。

本次未执行部署、提交或推送。HTML 文件产物、MySQL 查询执行与通用 Workflow 产品化保持后续范围。仓库其他未提交修改均保留。根格式检查通过，当前 Feature 另行定点格式检查；综合 check/build 限定 Agent、Web、Worker、Memory，home 与 data-seed 的构建或测试会读取其他 Feature，按访问约束排除。

最终补充：进程内 external_write / unknown 工具沿用与远端相同的持久回执边界。独立 PostgreSQL 专项共 7 项通过，包含审批后 action 状态和接受回执重放，确认物理副作用不重复执行。Worker 的 27 项单元及静态检查通过。

综合入口：pnpm check -F agent -F web -F worker -F memory 通过（13 项任务）；pnpm build -F agent -F web -F worker -F memory 通过（5 项任务）。根格式、脚本 lint、契约漂移和 workspace 依赖边界一并通过。最终 git diff --check 与当前 Feature 的定点 Prettier 检查通过。
