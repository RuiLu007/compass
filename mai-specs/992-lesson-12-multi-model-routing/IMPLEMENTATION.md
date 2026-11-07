# Feature 992 实施与验收

> 2026-11-05 | 本期目标：两级意图分流、字典特征、契约选模、一次Runtime推理与Chat响应展示。

## 交付行为

前端统一使用`models/calls`，每轮一次POST；服务端完成JEV优先/Flash分类兜底、两级Intent校验、字典特征提取、fixed/auto契约选模和一次现有Runtime调用。响应包含intent/features/routing/result/metrics，回答下方可展开“意图分类与响应结构”。

静态选择仅约束业务主模型；SC-39闲聊优先固定Flash，使用独立prompt温和婉拒并询问销售、库存等业务数据任务。系统优化L1使用Flash，L2/L3使用Pro，缺信息等场景按字典输出相应响应。

分类对象严格两个字段，场景必须属于一级分类。特征由40场景字典查表产生；JSON从工作簿场景目录导出，不含88题金标。规则/默认门槛/时限在代码中，角色UUID可通过已有环境装配；根dev入口同步透传角色配置。

没有新增数据库表、迁移、管理API或策略页面；没有完整Harness、Skill/subAgent/workflow分派、多步骤计划或Runtime恢复执行器。效果调优、阈值校准及正式准确率评测留后续课程，Runtime可靠性留第14课。

## 文件与接口

- `apps/agent/src/compass_agent/routing/`：分类、代码策略、字典、prompt与薄应用服务。
- `apps/agent/scripts/export_routing_dictionary.py`：只读导出场景字典并检查漂移。
- `schemas.py`、`model_api.py`：统一调用输入与整轮JSON/SSE响应；Provider诊断保留原协议。
- `models/service.py`、`repository.py`：复用单调用生命周期、内部分类授权、父/子时限与记录，沿用chat/test现有数据库约束。
- Web模型BFF/客户端/Hook与分析输入框：严格契约与事件顺序，route_selected后核验实际Provider，分类结构随成功回答保留。
- `.env.example`、`turbo.json`和应用README：角色配置、根命令环境透传与新行为说明。

## 已执行验证

| 验证                                                                                      | 本次结果                                                                                                                |
| ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `pnpm check`                                                                              | 通过；57项工具测试，全部格式/lint/类型/契约/边界及应用测试通过，其中Agent 167项、Web 55项                               |
| `pnpm build`                                                                              | 通过；7项任务，Web生产构建和Python分发包生成完成                                                                        |
| `pnpm test:integration -F agent`（专用PostgreSQL17）                                      | 18项通过，含新增分类/推理真实元记录与闲聊Flash路由；旧迁移/恢复回归通过                                                 |
| `pnpm test:integration -F web`（专用PostgreSQL17）                                        | 22项通过，含模型授权与Web基座回归                                                                                       |
| `pnpm test:e2e -F web -- intent-routing.spec.ts analysis-composer.spec.ts models.spec.ts` | 16项通过，真实Web/BFF/Agent/数据库及受控上游；分类结构、闲聊Flash、375/768/1440、停止、撤权、应用切换与Provider诊断通过 |
| 字典导出`--check`                                                                         | 通过；40个唯一场景、两级归属与确定性特征，未导出问题金标                                                                |
| Python wheel内容检查                                                                      | `compass_agent-0.1.0-py3-none-any.whl`包含routing模块与scenes.json                                                      |
| 真实供应商最小调用                                                                        | 销售对比命中SC-02并由Flash正常分析；闲聊命中SC-39，Flash真实输出婉拒及业务引导；两次均completed                         |
| 文档与差异                                                                                | 格式、内部链接、路径、验收编号与`git diff --check`检查通过                                                              |

真实模型本次使用现有JEV/DeepSeek接入。JEV未产生本次可采用分类时，实际走Flash兜底，两条最终链路均成功；受控上游另覆盖JEV合法结果直接采用路径。未进行分类效果调优、准确率统计或性能收益宣称。

## 验收映射

分类/字典/选模与固定模型：`test_intent_routing.py`覆盖两字段Schema、归属、字典L1/L3、fixed/auto及闲聊覆盖，关联AC-992-02～08、43、48、50～52。

异常/停止与记录：同测试覆盖JEV不确定→Flash、非法Flash停止、重复ID、内部grant、生成失败无重试、分类取消与流式顺序；真实PostgreSQL新增用例验证两次物理调用的作用域/状态和不存对话正文，关联AC-992-10～12、26、38、49、53。

界面端到端：新`intent-routing.spec.ts`验证输入→两级Intent→字典特征→实际模型和完整响应；闲聊在静态Pro下仍用Flash并显示专用引导。既有输入框与模型测试回归覆盖布局、键盘、撤权、停止和上下文，关联AC-992-24、27、28、30～32、34、35、37、56、57。

## 本地复现与产物

按应用README使用专用测试数据库，不使用日常库。受控Agent宿主是现有`apps/agent/tests/e2e_app.py`；它只替换上游HTTP，未替换Web、BFF、Agent或PostgreSQL。集成与浏览器使用独立Web测试数据库，防止测试清理互相影响。

本次运行日志、私有配置、真实供应商调用详情及浏览器截图保留在忽略目录`work/992/`，不提交Git。真实输入仅为合成指标和普通闲聊；未改真实经营数据、现有凭据或用户权限。课程Feature目录沿用原忽略规则，生产源码与契约由上层课程Git管理。

开发、测试与自检已完成；未提交、推送或部署。范围外工作继续保持后续课程安排。
