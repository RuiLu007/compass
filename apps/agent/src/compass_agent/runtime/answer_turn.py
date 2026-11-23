"""Answer protocol ports for AgentLoop.execute_tools; all work stays in the shared loop."""

import asyncio
import json
from dataclasses import replace
from hashlib import sha256

from compass_agent.context_runtime.answer_verification import (
    before_delivery,
    check_access,
    review_material,
)
from compass_agent.context_runtime.answers import ANSWER, AnswerReview, answer_schema
from compass_agent.context_runtime.source_access import read_private, save_private
from compass_agent.execution_context import current_execution, execution_path
from compass_agent.harness.contracts import RepeatStep, RunFailure
from compass_agent.runtime.context import generate_input
from compass_agent.runtime.private_state import digest
from compass_agent.schemas import RUN_STREAM_LIMITS, AnalysisFileNone, ChatMessage, RunCompleted


def needs_delivery_reserve(budget, route, output_tokens):
    """Forecast delivery headroom from settled usage; hard limits stay in the budget."""
    if budget is None:
        return False
    if budget.steps >= budget.limits.steps - 3:
        return True
    if not budget.attempts:
        return False
    profiles = [
        p
        for p in budget.profiles
        if (p.provider_id, p.binding_version) == (route.provider_id, route.binding_version)
    ]
    if not profiles:
        return False  # The budget's normal reservation reports unsupported profiles.
    # A profile's input bound may span the provider's entire context window.
    # Reserving it repeatedly would close tools before even a small task starts.
    tokens = budget.tokens // budget.attempts + output_tokens
    cost = budget.cost_microusd // budget.attempts + max(
        output_tokens * p.output_microusd for p in profiles
    )
    child_tokens = sum(c.limits.tokens for c in budget.children)
    child_cost = sum(c.limits.cost_microusd for c in budget.children)
    return (
        budget.tokens + child_tokens + 3 * tokens > budget.limits.tokens
        or budget.cost_microusd + child_cost + 3 * cost > budget.limits.cost_microusd
    )


async def execute_answer_turn(
    executor, state, route, trusted, control, controlled, emit, *, max_steps=None
):
    journal = current_execution.get()
    assert journal is not None
    path = execution_path.get()
    if route.deliverable not in {"business_qa", "data_analysis", "dashboard", "business_report"}:
        route = replace(route, deliverable="business_qa")
    budget = controlled.budget if controlled else None
    if controlled:
        journal.state_providers[path] = controlled.checkpoint_state
    question = next((m.content or "" for m in reversed(state.messages) if m.role == "user"), "")
    journal.context_questions[path] = question
    from compass_agent.context_runtime.evidence import user_input

    input_id = await asyncio.to_thread(user_input, journal, question)
    state.messages.append(
        ChatMessage(
            role="user",
            content=json.dumps(
                {
                    "userInput": {
                        "evidenceId": input_id,
                        "paragraphId": "user-input",
                        "source": "用户提供",
                    },
                    "instruction": (
                        "仅在引用用户提供的原文时使用此定位；对企业事实的核对仍根据问题调用工具。"
                    ),
                },
                ensure_ascii=False,
            ),
        )
    )
    turns = getattr(journal, "answer_turn", {})
    turn = turns.setdefault(path, {})
    journal.answer_turn = turns
    pending = None
    working_messages = None
    delivery_only = False
    model_seconds = 0.0
    work_seconds = 0.0
    definitions = state.tools.definitions()
    entries = state.tools.runtime.registry.entries
    completion_names = {name for name, entry in entries.items() if entry.completion}
    fallback = not route.tools_enabled
    if fallback and not route.schema_output:
        raise RunFailure("model_unavailable", reason="answer_protocol_unsupported")

    def evidence():
        return getattr(journal, "answer_evidence", {}).get(path, {})

    def feedback(next_action, text):
        state.messages.append(
            ChatMessage(
                role="user",
                content=json.dumps(
                    {"verification": {"next": next_action, "feedback": text}}, ensure_ascii=False
                ),
            )
        )
        state.continuation_token = None

    def prepare():
        nonlocal working_messages, delivery_only
        reviewing = pending is not None
        turn["purpose"] = "verify" if reviewing else "work"
        turn["schema"] = (
            AnswerReview.model_json_schema(by_alias=True)
            if reviewing
            else answer_schema()
            if fallback
            else None
        )
        if reviewing:
            working_messages = list(state.messages)
            state.messages = [
                ChatMessage(
                    role="user",
                    content=review_material(
                        question,
                        pending,
                        evidence(),
                        {"seconds": control.remaining, "workToolsAvailable": not delivery_only},
                    ),
                )
            ]
            if not route.schema_output:
                state.messages[0] = ChatMessage(
                    role="user",
                    content=(state.messages[0].content or "")
                    + "\n输出 JSON Schema："
                    + json.dumps(turn["schema"]),
                )
                turn["schema"] = None
            state.continuation_token = None
        journal.context_phase = "analyze" if reviewing else "query"
        request = generate_input(
            executor.definition,
            state.messages,
            state.continuation_token,
            route,
            controlled=controlled is not None,
            defer_budget=True,
        )
        from compass_agent.context_runtime.model_step import model_limits

        limits = model_limits(route, budget)
        output_caps = (
            [
                p.output_tokens
                for p in budget.profiles
                if (p.provider_id, p.binding_version) == (route.provider_id, route.binding_version)
            ]
            if budget
            else []
        )
        request = request.model_copy(
            update={
                "max_tokens": min(
                    8192, limits.max_output_tokens or request.max_tokens, *output_caps
                )
            }
        )
        selected = [] if reviewing or fallback else definitions
        limit = min(
            max_steps or RUN_STREAM_LIMITS["steps"],
            budget.limits.steps if budget else RUN_STREAM_LIMITS["steps"],
        )
        if (
            not reviewing
            and not fallback
            and (
                len(state.steps) >= limit - 1
                or (budget and budget.tools >= budget.limits.tools)
                or needs_delivery_reserve(budget, route, request.max_tokens)
                or (len(state.steps) > 1 and control.remaining <= 3 * model_seconds + work_seconds)
            )
        ):
            delivery_only = True
            state.continuation_token = None
            selected = [tool for tool in definitions if tool.function.name in completion_names]
            request = request.model_copy(
                update={
                    "continuation_token": None,
                    "messages": [
                        *request.messages,
                        ChatMessage(
                            role="user",
                            content=(
                                "请在剩余预算内提交 final_answer。"
                                "仍有缺口时使用 partial，并明确未解决事项。"
                            ),
                        ),
                    ],
                }
            )
        return request.model_copy(update={"tools": selected}), None

    def read_candidate(identifier):
        with journal.store.transaction() as tx:
            value = read_private(
                journal.store, tx, journal.run_id, f"answer/v2/candidate/{identifier}"
            )
        if not value or value["executionPath"] != path:
            raise RunFailure("internal_error", reason="candidate_scope_mismatch")
        return value

    async def decide(record, review=None):
        nonlocal pending
        decision = before_delivery(journal.run_id, record, evidence(), review)
        from compass_agent.context_runtime.answer_artifacts import build_artifact

        try:
            if record["candidate"].get("artifact") and any(
                ref.get("kind") == "transient"
                for item in evidence().values()
                for ref in item["sourceRefs"]
            ):
                raise ValueError(
                    "当前来源只支持即时读取。请改用支持历史读取的来源生成附件，"
                    "或提交部分回答并说明附件暂不可保存。"
                )
            artifact = build_artifact(
                ANSWER.validate_python(record["candidate"]),
                evidence(),
                decision.body,
                f"{path}/model:{state.steps[-1].id}",
                journal.context_date.isoformat(),
            )
        except ValueError as exc:
            pending = None
            feedback("revise", str(exc))
            return None
        if decision.action == "review":
            pending = record
            return None
        pending = None
        if decision.action != "accept":
            feedback(decision.action, decision.feedback)
            return None
        if not await asyncio.to_thread(check_access, journal, trusted):
            raise RunFailure("internal_error", reason="answer_source_access_changed")
        acceptance = {
            "candidateId": record["id"],
            "candidateDigest": record["digest"],
            "evidenceRevision": digest(evidence()),
            "textDigest": digest(decision.body),
            "citationsDigest": digest(decision.citations),
            "review": review,
            "mode": record["candidate"]["mode"],
            "completion": record["candidate"]["completion"],
            "executionPath": path,
            "artifactDigest": sha256(artifact.data).hexdigest() if artifact else None,
        }

        def save():
            with journal.store.transaction() as tx:
                _, run = journal.store.lock(tx, journal.run_id)
                journal.store.require_epoch(run, journal.epoch)
                save_private(
                    journal.store, tx, run, f"answer/v2/accepted/{record['id']}", acceptance, []
                )

        await asyncio.to_thread(save)
        completed = RunCompleted(
            status="completed", finalAnswer=decision.body, file=AnalysisFileNone(status="none")
        )
        completed._citations = decision.citations
        completed._answer_acceptance = acceptance
        completed._delivery = artifact
        return completed

    async def accept(step, result, prepared):
        nonlocal pending, working_messages, model_seconds
        model_seconds = max(model_seconds, getattr(result, "duration_ms", 0) / 1000)
        if turn["purpose"] == "verify":
            state.messages = working_messages or []
            working_messages = None
            state.continuation_token = None
            if result.tool_calls:
                pending = None
                feedback("revise", "审阅阶段只接受结构化校验结果，请重新提交候选。")
                return RepeatStep()
            try:
                review = json.loads(result.text)
            except (ValueError, TypeError):
                pending = None
                feedback("revise", "审阅输出格式无效，请重新提交候选。")
                return RepeatStep()
            record = pending
            assert record is not None
            return await decide(record, review) or RepeatStep()
        if result.tool_calls:
            if len(result.tool_calls) > RUN_STREAM_LIMITS["toolsPerStep"]:
                feedback(
                    "revise",
                    f"本轮最多调用 {RUN_STREAM_LIMITS['toolsPerStep']} 个工具。"
                    "本批尚未执行，请拆为多个批次，先提交当前需要的调用。",
                )
                return RepeatStep()
            if state.trace:
                state.trace.bind_calls(str(step.id), tuple(result.tool_calls), entries)
            return None
        if fallback:
            try:
                value = ANSWER.validate_json(result.text).model_dump(mode="json", by_alias=True)
            except ValueError as exc:
                feedback("revise", str(exc))
                return RepeatStep()
            from compass_agent.context_runtime.answer_candidates import submit
            from compass_agent.tools.contracts import ToolExecutionContext

            context = ToolExecutionContext(
                trusted,
                control.id,
                step.id,
                control,
                state.tools.budget,
                frozenset(),
                call_id=f"answer-{step.index}",
            )
            receipt = await submit(value, context)
            assert isinstance(receipt.payload, dict)
            return (
                await decide(
                    await asyncio.to_thread(read_candidate, receipt.payload["candidateId"])
                )
                or RepeatStep()
            )
        if result.text:
            state.messages.append(ChatMessage(role="assistant", content=result.text))
        feedback(
            "revise", "执行说明已记录。请通过 final_answer 提交最终正文；需要补查时继续调用工具。"
        )
        return RepeatStep()

    async def dispatch(step, request):
        nonlocal work_seconds
        started = asyncio.get_running_loop().time()
        waiting = await state.tools.dispatch(step, request.tools)
        if not any(call.function.name in completion_names for call in step.tool_calls):
            work_seconds = max(work_seconds, asyncio.get_running_loop().time() - started)
        if waiting:
            return waiting
        for call, result in zip(step.tool_calls, state.tools.last_results, strict=True):
            if call.function.name in completion_names and result.status == "ok":
                record = await asyncio.to_thread(read_candidate, result.payload["candidateId"])
                return await decide(record)
        return None

    def public_response(result):
        return (
            turn["purpose"] != "verify"
            and not fallback
            and not any(call.function.name in completion_names for call in result.tool_calls)
        )

    async def incomplete(step, result):
        nonlocal pending, working_messages
        if turn["purpose"] == "verify":
            state.messages = working_messages or []
            working_messages = None
            pending = None
            feedback("revise", "审阅输出未完成，请缩短候选并重新提交。")
            return RepeatStep()
        if result.error_code == "output_truncated" or result.finish_reason == "length":
            feedback(
                "revise",
                "本轮输出被截断，本批调用尚未执行。请减少工具批次或缩短候选的正文、"
                "结论和缺口说明，再完整提交；已有证据继续可用。",
            )
            return RepeatStep()
        return None

    return await executor.execute_tools(
        state,
        route,
        trusted,
        control,
        emit,
        prepare,
        accept,
        dispatch,
        budget=budget,
        publish_text=False,
        model_timeout=None,
        public_response=public_response,
        handle_incomplete=incomplete,
    )
