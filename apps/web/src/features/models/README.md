# 模型界面

`CallTrace` 展示本轮 call_id / trace_id 与采样说明，收到响应头后即可复制，停止和断流时保留。`ObservabilityPanel` 仅在平台模型页挂载，读取本实例导出诊断与两个模型的固定窗口对比；复用 DocumentSession 的权限与导航清理。Langfuse 项目入口由 Web 服务端固定配置，项目 Key 用于 Agent 的导出与聚合请求。部署见 [观测说明](../../../../../deploy/observability/README.md)。

平台 Provider 配置、能力测试、工作区只读摘要与分析 Chat 共用的调用状态。所有请求通过 Web BFF；模型 DTO 使用 Agent 派生类型，运行时校验使用生成的 model-contracts schema。

`use-model-call.ts` 负责页面内调用与停止；`DocumentSession.modelRequest` 负责页面 generation、权限撤回、响应预算和 SSE 生命周期。模型输入、输出和凭据草稿仅驻留当前页面内存。切换应用不改变界面或调用状态，已发出的生成持续处理；后台完成的轮次继续参与下一轮聊天上下文。确认失权或退出时仍清空并 abort。

Provider 编辑器支持目录选择与手工 LiteLLM 标识；供应商连接参数按 JSON 填写，加密保存且不回显，空白保留、`{}` 清空。生成模型可由平台开放给 Chat，模型类型、可见性及权限由服务端复核。

System One 决策模型支持阿里云与 JEV 官方预设，也可手工填写 `aliyun/model`、`typesafe/model`、`systemone/model`，型号可独立修改。表单对决策配置要求 Base URL 与 API Key，隐藏 LiteLLM 连接参数并禁用 Chat；服务端执行最终校验。决策测试复用 Choice/Noul/Score 页面，输入/输出用量按实际响应展示。

决策测试页默认加载意图分流示例，另提供是否升级、严重度评分和 JSON 多问题示例；加载示例只更新当前输入，实际调用由“运行测试”触发。自定义输入或结果存在时，替换前确认。输出“原始响应”标签展示 System One 上游 JSON（仅页面内存，不写入调用记录）；其他模型展示测试接口 JSON，流式调用展示汇总结果。切换示例、新调用、退出和撤权沿现有页面生命周期清理结果。
