"""Stable public failures. Upstream bodies and exceptions never become messages."""

MESSAGES = {
    "runtime_overloaded": "当前模型调用较多，请稍后重试。",
    "resource_cleanup_failed": "本次模型连接未能正常结束，请稍后重试。",
    "intent_invalid": "意图分类结果不符合场景字典。",
    "intent_unavailable": "暂时无法识别任务，请稍后重新提问。",
    "route_binding_missing": "意图路由模型尚未配置，请联系平台管理员。",
    "validation_failed": "请检查输入内容。",
    "unauthenticated": "服务认证失败。",
    "forbidden": "无权进行此操作。",
    "not_found": "模型或记录不存在。",
    "version_conflict": "配置已变化，请重新加载。",
    "operation_conflict": "操作标识已被其他请求使用。",
    "alias_conflict": "模型别称已存在。",
    "call_already_exists": "本次调用已受理，请核对结果。",
    "chat_model_not_supported": "该模型的输入类型不适用于分析 Chat。",
    "model_unavailable": "模型已停用或已从 Chat 隐藏。",
    "capability_not_supported": "该模型不支持所选能力。",
    "endpoint_not_allowed": "模型端点未在允许范围内。",
    "model_authentication_failed": "模型认证失败，请检查 API Key 与接入权限。",
    "model_rate_limited": "模型服务暂时限流，请稍后重试。",
    "model_timeout": "本次调用已超时。",
    "model_unavailable_upstream": "模型服务暂不可用。",
    "provider_protocol_error": "模型响应格式异常。",
    "stream_interrupted": "输出中断，当前结果不完整。",
    "output_truncated": "输出达到长度限制，当前结果不完整。",
    "model_refused": "模型拒绝了本次请求。",
    "empty_output": "模型未返回有效内容。",
    "invalid_json": "输出不是有效 JSON。",
    "schema_validation_failed": "输出不符合指定结构。",
    "invalid_tool_arguments": "工具调用参数不完整或不符合声明。",
    "continuation_invalid": "工具续接已失效，请重新开始测试。",
    "continuation_too_large": "工具续接状态过大，请减少输入并重新测试。",
    "response_too_large": "模型输出超过本次显示上限。",
    "record_write_failed": "模型调用记录保存失败，请核对调用结果。",
    "unavailable": "模型服务配置或数据库暂不可用。",
    "internal_error": "模型调用暂时失败。",
}


class ModelError(Exception):
    def __init__(self, code: str, status: int = 422, current_version: int | None = None):
        self.code = code
        self.status = status
        self.current_version = current_version
        super().__init__(code)


def message(code: str) -> str:
    return MESSAGES.get(code, MESSAGES["internal_error"])
