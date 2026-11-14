export const MODEL_ERRORS = {
  runtime_overloaded: { message: "当前模型调用较多，请稍后重试。", status: 503 },
  resource_cleanup_failed: { message: "本次模型连接未能正常结束，请稍后重试。", status: 503 },
  version_conflict: { message: "配置已变化，请重新加载。", status: 409 },
  operation_conflict: { message: "操作标识已被其他请求使用。", status: 409 },
  alias_conflict: { message: "模型别称已存在。", status: 409 },
  call_already_exists: { message: "本次调用已受理，请核对结果。", status: 409 },
  chat_model_not_supported: { message: "该模型的输入类型不适用于分析 Chat。", status: 422 },
  model_unavailable: { message: "模型已停用或已从 Chat 隐藏。", status: 409 },
  capability_not_supported: { message: "该模型不支持所选能力。", status: 422 },
  endpoint_not_allowed: { message: "模型端点未在允许范围内。", status: 422 },
  model_authentication_failed: { message: "模型认证失败，请检查接入配置。", status: 503 },
  model_rate_limited: { message: "模型服务暂时限流，请稍后重试。", status: 429 },
  model_timeout: { message: "本次调用已超时。", status: 503 },
  model_unavailable_upstream: { message: "模型服务暂不可用。", status: 503 },
  provider_protocol_error: { message: "模型响应格式异常。", status: 503 },
  stream_interrupted: { message: "输出中断，当前结果不完整。", status: 503 },
  input_token_limit_exceeded: {
    message: "模型拒绝了超出输入或上下文窗口限制的请求。",
    status: 422,
  },
  output_token_limit_exceeded: { message: "模型拒绝了请求的输出 token 上限。", status: 422 },
  invalid_model_input: { message: "模型拒绝了本次请求的输入或生成参数。", status: 422 },
  catalog_token_limits_unavailable: {
    message: "当前模型目录没有可导入的 token 上限。",
    status: 422,
  },
  invalid_generation_defaults: { message: "默认输出 token 超过配置允许的范围。", status: 422 },
  generation_profile_review_required: {
    message: "更换型号或端点时，请重新提交或清空生成配置。",
    status: 422,
  },
  output_truncated: { message: "输出达到长度限制，当前结果不完整。", status: 422 },
  model_refused: { message: "模型拒绝了本次请求。", status: 422 },
  empty_output: { message: "模型未返回有效内容。", status: 503 },
  invalid_json: { message: "模型输出的 JSON 无效。", status: 422 },
  schema_validation_failed: { message: "输出不符合指定结构。", status: 422 },
  invalid_tool_arguments: { message: "工具调用参数不完整或不符合声明。", status: 422 },
  continuation_invalid: { message: "工具续接已失效，请重新开始测试。", status: 422 },
  continuation_too_large: { message: "工具续接状态过大，请减少输入。", status: 422 },
  response_too_large: { message: "模型输出超过本次显示上限。", status: 503 },
  record_write_failed: { message: "模型调用记录保存失败，请核对调用结果。", status: 503 },
  internal_error: { message: "模型调用暂时失败。", status: 503 },
} as const;

/** Stable safe wording for streamed and synchronous model failures. */
export function modelErrorMessage(code: string): string {
  return Object.hasOwn(MODEL_ERRORS, code)
    ? MODEL_ERRORS[code as keyof typeof MODEL_ERRORS].message
    : "模型调用暂时失败，请稍后手动重试。";
}
