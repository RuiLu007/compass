"""Run finite scenarios through the actual Runtime and ReliableExecutor."""

import asyncio
from uuid import UUID, uuid5

from cryptography.fernet import Fernet
from pydantic import SecretStr

from compass_agent.llm.admission import ExecutionGate
from compass_agent.llm.contracts import (
    ContinuationBinding,
    ExecutionRequest,
    InvocationContext,
    ModelSpec,
    ProviderAccess,
    RuntimeCancelled,
)
from compass_agent.llm.executor import ReliableExecutor
from compass_agent.llm.litellm_chat import LiteLLMChatAdapter
from compass_agent.llm.policy import CallPolicy, LogicalPolicy
from compass_agent.llm.runtime import Runtime
from compass_agent.models.secrets import ModelContinuationCodec, SecretStore
from compass_agent.models.service import to_response
from compass_agent.prompt_assets import prompt_text
from compass_agent.reliability.provider import ScriptedProvider
from compass_agent.schemas import (
    ChatMessage,
    GateSnapshot,
    GenerateInput,
    PlatformScope,
    ShowcaseCall,
    ShowcaseCatalog,
    ShowcaseProgress,
    ShowcaseReport,
    ShowcaseScenario,
)

CATALOG = ShowcaseCatalog(
    items=[
        ShowcaseScenario.model_validate(
            dict(id=id, title=title, description=description, expected=expected)
        )
        for id, title, description, expected in [
            ("success", "正常完成", "完整流式输出与成功提交", "1 次尝试，成功结果 1 份"),
            ("rate_limit", "429 后恢复", "首次限流，随后恢复", "同一调用 2 次尝试，未知用量保留"),
            ("unavailable", "503 后恢复", "首次服务不可用，随后恢复", "2 次尝试，同一模型及输入"),
            ("exhausted", "重试次数耗尽", "连续两次返回限流", "最多 2 次，停止恢复"),
            ("authentication", "认证失败即停止", "模拟供应商返回 401", "仅 1 次，不重试"),
            ("timeout", "已发送后超时", "供应商已接收但迟迟不返回", "1 次，结果未知，不重试"),
            ("interrupted", "输出半途断流", "已显示正文后连接中断", "保留预览，禁止提交及重试"),
            (
                "schema_invalid",
                "结构校验拦截",
                "完整 JSON 的字段类型错误",
                "无效输出不提交，不自动修复",
            ),
            ("cancel", "取消正在执行", "首个分片后自动发起取消", "停止出站，清理后归还容量"),
            (
                "concurrency",
                "并发与过载",
                "20 条调用竞争 K=2、N=5 的共享闸门",
                "活跃≤2、进入≤5，其余明确过载",
            ),
            ("cleanup_failed", "关闭失败隔离", "模拟资源关闭失败", "停止并隔离执行槽"),
        ]
    ]
)


def matches(scenario, calls, gate):
    if scenario == "concurrency":
        return (
            len(calls) == 20
            and gate.peak_active <= 2
            and gate.peak_inflight <= 5
            and sum(c.result.error_code == "runtime_overloaded" for c in calls) == 15
            and sum(c.accepted for c in calls) == 5
            and gate.inflight == 0
            and gate.active == 0
        )
    call = calls[0]
    expected = {
        "success": ("completed", 1),
        "rate_limit": ("completed", 2),
        "unavailable": ("completed", 2),
        "exhausted": ("failed", 2),
        "authentication": ("failed", 1),
        "timeout": ("failed", 1),
        "interrupted": ("incomplete", 1),
        "schema_invalid": ("incomplete", 1),
        "cancel": ("canceled", 1),
        "cleanup_failed": ("incomplete", 1),
    }
    status, attempts = expected[scenario]
    result = call.result
    return (
        result.status == status
        and result.execution is not None
        and len(result.execution.attempts) == attempts
        and call.upstream_requests == attempts
        and call.accepted == (status == "completed")
        and (scenario != "interrupted" or bool(call.preview))
        and (scenario != "timeout" or result.upstream_outcome_unknown)
        and gate.inflight == 0
        and gate.active == 0
        and gate.quarantined == (1 if scenario == "cleanup_failed" else 0)
    )


async def run_showcase(command, context, emit):
    loop = asyncio.get_running_loop()
    started = loop.time()
    scenario = command.scenario
    gate = ExecutionGate(2, 5)
    codec = ModelContinuationCodec(SecretStore(Fernet.generate_key().decode()))
    calls, tasks = [], []

    async def one(number):
        call_id = uuid5(command.call_id, str(number))
        provider_id = UUID("99100000-0000-4000-8000-000000000001")
        script = ScriptedProvider(scenario)
        runtime = Runtime({"litellm_chat": LiteLLMChatAdapter(codec, script.transport)})
        preview = ""
        task = None

        async def progress(message, text=""):
            await emit(
                ShowcaseProgress(
                    call_id=command.call_id,
                    number=number,
                    message=message,
                    text=text,
                    capacity=GateSnapshot.model_validate(gate.snapshot()),
                )
            )

        script.on_event = progress

        async def delta(event):
            nonlocal preview
            if event.type == "text_delta":
                preview += event.text
                await progress("接收预览分片", event.text)
                if scenario == "cancel" and task is not None:
                    loop.call_later(0.02, task.cancel)

        spec = ModelSpec(
            provider_id,
            1,
            1,
            "deepseek",
            "llm",
            "litellm_chat",
            "deepseek-v4-pro",
            "deepseek/deepseek-v4-pro",
            "https://simulation.invalid",
        )
        value = GenerateInput(
            messages=[
                ChatMessage(
                    role="user", content=prompt_text("support/diagnostics/reliability-request.md")
                )
            ],
            stream=True,
        )
        if scenario == "schema_invalid":
            value.output_format = "schema"
            value.output_schema = {
                "type": "object",
                "properties": {"sales": {"type": "integer"}},
                "required": ["sales"],
            }
        now = loop.time()
        request = ExecutionRequest(
            InvocationContext(
                call_id,
                "diagnostic",
                ContinuationBinding(
                    actor=context.actor_user_id,
                    scope=PlatformScope(kind="platform"),
                    provider=provider_id,
                    binding_version=1,
                ),
            ),
            ProviderAccess(spec, SecretStr("simulation-only")),
            value,
            CallPolicy(now, now + (0.7 if scenario == "timeout" else 8)),
        )
        await progress("开始逻辑调用")
        task = asyncio.create_task(
            ReliableExecutor(runtime, gate).execute(request, LogicalPolicy(max_attempts=2), delta)
        )
        try:
            try:
                result = await task
            except RuntimeCancelled as exc:
                owner = asyncio.current_task()
                if owner is not None and owner.cancelling():
                    raise
                result = exc.result
            response = to_response(result)
            calls.append(
                ShowcaseCall(
                    number=number,
                    result=response,
                    preview=preview,
                    upstream_requests=script.requests,
                    accepted=result.status == "completed",
                )
            )
            await progress("调用结束：" + result.status)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    try:
        tasks = [
            asyncio.create_task(one(n + 1)) for n in range(20 if scenario == "concurrency" else 1)
        ]
        async with asyncio.timeout(20):
            await asyncio.gather(*tasks)
        calls.sort(key=lambda c: c.number)
        return ShowcaseReport(
            call_id=command.call_id,
            scenario=scenario,
            passed=matches(scenario, calls, gate),
            duration_ms=int((loop.time() - started) * 1000),
            calls=calls,
            capacity=GateSnapshot.model_validate(gate.snapshot()),
        )
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
