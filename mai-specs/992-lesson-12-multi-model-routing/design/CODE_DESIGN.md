# Feature 992 核心代码设计与实现索引

[返回技术方案](TECH_DESIGN.md)

本期核心链路已落实到生产源码，以下索引维护职责和评审不变量；代码仅在对应模块维护一份。

| 模块                                                                         | 实现与关键约束                                                                                                   |
| ---------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| [schemas.py](../../../apps/agent/src/compass_agent/schemas.py)               | Intent严格两个字段；ChatCallInput兼容provider_id和main_model且互斥；RoutedCallResponse分层；Provider诊断协议独立 |
| [intent.py](../../../apps/agent/src/compass_agent/routing/intent.py)         | JEV两题归一化；默认概率/间隔校验及父子关系；Flash同一严格Schema，额外业务字段拒绝                                |
| [dictionary.py](../../../apps/agent/src/compass_agent/routing/dictionary.py) | 只读40场景，特征查表，不使用模型生成difficulty                                                                   |
| [service.py](../../../apps/agent/src/compass_agent/routing/service.py)       | 最多一次JEV、一次Flash分类、一次生成；业务fixed/auto；SC-39优先固定Flash；失败无恢复循环                         |
| [prompts.py](../../../apps/agent/src/compass_agent/routing/prompts.py)       | 闲聊独立prompt，温和婉拒并询问业务数据任务；业务prompt限定当前数据和能力边界                                     |
| [models/service.py](../../../apps/agent/src/compass_agent/models/service.py) | 复用prepare/run和调用记录，新增父/子deadline与内部grant传递，无Runtime可靠性增量                                 |
| [repository.py](../../../apps/agent/src/compass_agent/models/repository.py)  | 分类grant校验actor/scope/call_id/Provider/kind，复用chat/test现有表约束                                          |
| [model_api.py](../../../apps/agent/src/compass_agent/model_api.py)           | 统一models/calls完成JSON或SSE路由链；start不预先固定Provider，route_selected后实际推理                           |

选模的核心顺序是：SC-39→专用Flash；其余fixed→用户模型；auto且L2/L3→Pro；其余auto→Flash。分类器角色始终独立于主模型选择。指定模型不满足能力时按现有错误返回，不隐式更换模型。

整轮call_id用于主推理记录；分类物理ID由UUID5(call_id,阶段)派生。重复阶段返回冲突并停止，未建设整轮持久化重放。取消、记录失败、父时限等沿既有单次生命周期处理，不捕获成无限重试。

SSE/BFF/client依同一生成契约验证call_id、事件顺序、选定Provider与唯一final；Chat保留页面内成功上下文，错误/不完整回答不进入下一轮。可展开详情展示intent/features/routing/result/metrics，分类元信息与意图两个字段分开。

代码默认门槛用于流程演示；分类准确率、阈值校准、性能收益、完整Harness和Runtime第14课可靠性均留后续课程。
