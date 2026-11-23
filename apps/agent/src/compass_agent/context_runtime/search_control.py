"""Run-scoped search decisions and metered, replayable read operations."""

import asyncio
import json
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid5

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from compass_agent.context_runtime.source_access import (
    read_private,
    require_search_sources,
    save_private,
)
from compass_agent.documents.client import access_digest
from compass_agent.execution_context import execution_path
from compass_agent.models.errors import ModelError
from compass_agent.runtime.entities import AgentMessage
from compass_agent.runtime.private_state import digest
from compass_agent.tools.contracts import ExecutionReceipt, ToolRejected

READ_TOOLS = {
    "search_semantics",
    "get_metric_definition",
    "resolve_semantics",
    "query_metric",
    "database_query",
    "read_tool_result",
    "search_documents",
    "read_document",
}
SOURCES = Literal["semantic", "detail", "knowledge", "history", "external"]
TRANSIENT = {
    "source_unavailable",
    "service_unavailable",
    "rate_limited",
    "database_unavailable",
    "document_service_unavailable",
    "semantic_service_unavailable",
}


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    key: str = Field(pattern=r"^[^\W\d][\w-]{0,39}$")
    goal: str = Field(min_length=1, max_length=200)
    source: SOURCES
    required: bool = True


def parse_targets(text):
    try:
        obj = json.loads(text or "")
        if (
            not isinstance(obj, dict)
            or set(obj) != {"targets"}
            or not isinstance(obj["targets"], list)
        ):
            return None
        if not 1 <= len(obj["targets"]) <= 12:
            return None
        items = [Target.model_validate(t) for t in obj["targets"]]
        if len({t.key for t in items}) == len(items):
            return items
    except (ValueError, TypeError, ValidationError):
        pass
    return None


def query_key(pin, context, prepared):
    args = dict(prepared.args)
    # Preserve SQL, identifier case and string literals. Only the discovery question
    # uses a whitespace-insensitive matching contract.
    if prepared.tool.name in {"search_documents", "search_semantics"} and isinstance(
        args.get("query"), str
    ):
        args["query"] = " ".join(args["query"].split())
    return digest(
        [str(context.run_id), [str(v) for v in context.owner], pin, prepared.tool.name, args]
    )


def cacheable(receipt, tool):
    if (
        receipt.status != "ok"
        or receipt.cleanup != "confirmed"
        or not isinstance(receipt.payload, dict)
    ):
        return False
    payload = receipt.payload
    complete = not payload.get("truncated") or tool == "search_documents"
    return complete and payload.get("status", "ok") not in {
        "error",
        "unavailable_binding",
        "needs_clarification",
        "SOURCE_DELAYED",
        "COVERAGE_UNKNOWN",
    }


class SearchDispatch:
    """The Run row locks its physical allowance. Journal replay restores logical calls."""

    def __init__(self, journal, pin, budget, restore_plan=None):
        self.journal, self.pin, self.budget = journal, pin, budget
        self.locks = defaultdict(asyncio.Lock)
        self.restored: set[str] = set()
        self.restore_plan = restore_plan

    def reserved_count(self):
        with self.journal.store.transaction() as tx:
            _, run = self.journal.store.lock(tx, self.journal.run_id)
            self.journal.store.require_epoch(run, self.journal.epoch)
            ledger = read_private(self.journal.store, tx, run.id, "search-allowance")
            return len(ledger or [])

    def _reserve(self, context, key, identity, *, reuse, service=None):
        journal = self.journal
        with journal.store.transaction() as tx:
            _, run = journal.store.lock(tx, context.run_id)
            journal.store.require_epoch(run, journal.epoch)
            if reuse and (cached := read_private(journal.store, tx, run.id, "search-cache/" + key)):
                return cached
            purpose = "search-allowance"
            ledger = read_private(journal.store, tx, run.id, purpose) or []
            if any(entry["identity"] == identity for entry in ledger):
                raise ToolRejected("search_dispatch_uncertain")
            if len(ledger) >= self.budget.limits.tools:
                raise ToolRejected("max_tool_calls")
            entry = {"key": key, "identity": identity}
            if service:
                entry["service"] = service
                calls = [e for e in ledger if e.get("service") == service]
                settled = [e for e in calls if e.get("settled")]
                if len(settled) >= 2 and all(e.get("outage") for e in settled[-2:]):
                    failed_probe = any(e.get("serviceProbe") for e in settled[-2:])
                    if (
                        failed_probe
                        or any(not e.get("settled") for e in calls)
                        or datetime.now(UTC).timestamp()
                        < max(e.get("retryAt", 0) for e in settled[-2:])
                    ):
                        raise ToolRejected("source_blocked")
                    entry["serviceProbe"] = True
            # A previous unknown result can never trigger a new physical invocation.
            matching = [entry for entry in ledger if entry["key"] == key]
            if reuse and any(not entry.get("settled") for entry in matching):
                raise ToolRejected("search_dispatch_uncertain")
            if (
                reuse
                and matching
                and matching[-1].get("failure")
                and (not matching[-1].get("retryable") or len(matching) >= 2)
            ):
                raise ToolRejected("source_blocked")
            ledger.append(entry)
            self._save_ledger(tx, run, ledger)
        return None

    def _save_ledger(self, tx, run, value):
        store = self.journal.store
        purpose = "search-allowance"
        row = tx.get(AgentMessage, uuid5(run.id, "context:" + purpose))
        if row is None:
            save_private(store, tx, run, purpose, value, None)
            return
        row.private_content = store.private.seal(run.id, purpose, value)
        row.revision += 1
        row.updated_at = datetime.now(UTC)

    def _settle(self, context, identity, key, receipt, *, tool, reuse):
        journal = self.journal
        with journal.store.transaction() as tx:
            _, run = journal.store.lock(tx, context.run_id)
            journal.store.require_epoch(run, journal.epoch)
            ledger = read_private(journal.store, tx, run.id, "search-allowance")
            if not isinstance(ledger, list):
                raise ToolRejected("search_dispatch_uncertain")
            entry = next(item for item in ledger if item["identity"] == identity)
            entry.update(
                settled=receipt.cleanup in {"confirmed", "not_started"},
                failure=receipt.status not in {"ok", "empty"},
                retryable=receipt.reason_code in TRANSIENT,
            )
            if entry.get("service"):
                entry["outage"] = receipt.reason_code in {
                    "document_service_unavailable",
                    "rate_limited",
                }
                if entry["outage"]:
                    delay = (
                        receipt.payload.get("retryAfterSeconds", 0)
                        if isinstance(receipt.payload, dict)
                        else 0
                    )
                    backoff = max(30, delay) if isinstance(delay, (int, float)) else 30
                    entry["retryAt"] = datetime.now(UTC).timestamp() + backoff
            self._save_ledger(tx, run, ledger)
            if reuse and cacheable(receipt, tool):
                save_private(journal.store, tx, run, "search-cache/" + key, asdict(receipt), None)

    async def invoke(self, prepared, context, live: Callable[[], Awaitable[ExecutionReceipt]]):
        key = query_key(self.pin, context, prepared)
        identity = f"{execution_path.get()}/search:{context.step_id}:{prepared.call_id}"
        read = prepared.tool.name in READ_TOOLS

        async def check():
            await asyncio.to_thread(
                require_search_sources,
                self.journal,
                context.trusted,
                epoch=self.journal.epoch,
            )

        executed = False

        async def dispatch():
            nonlocal executed
            executed = True
            # An accepted logical call keeps its stable identity on replay. New dispatches
            # use the current audience and policy when looking up a previous physical read.
            effective_key = key
            service = None
            if read:
                try:
                    audience = await asyncio.to_thread(
                        access_digest,
                        self.journal.store.source_settings,
                        context.trusted,
                        context.run_id,
                        prepared.tool.name,
                    )
                except ModelError as exc:
                    return {
                        "receipt": asdict(
                            ExecutionReceipt(
                                prepared.call_id, "rejected", "not_started", reason_code=exc.code
                            )
                        ),
                        "charges": 0,
                    }
                effective_key = digest([key, audience])
                if prepared.tool.name in {"search_documents", "read_document"}:
                    # Both operations use this Run's fixed collection on the same owner.
                    # A service outage persists across changes to the discovery wording.
                    service = digest(["documents", audience])
            async with self.locks[effective_key]:
                await check()
                charges = 0
                result: ExecutionReceipt | None = None
                for attempt in range(2):
                    attempt_id = f"{identity}/{attempt}"
                    try:
                        cached = await asyncio.to_thread(
                            self._reserve,
                            context,
                            effective_key,
                            attempt_id,
                            reuse=read,
                            service=service,
                        )
                    except ToolRejected as exc:
                        return {
                            "receipt": asdict(
                                ExecutionReceipt(
                                    prepared.call_id,
                                    "rejected",
                                    "not_started",
                                    reason_code=exc.code,
                                )
                            ),
                            "charges": charges,
                        }
                    if cached:
                        return {"receipt": cached, "charges": charges}
                    charges += 1
                    self.budget.tools += 1
                    context.on_start(prepared)
                    try:
                        result = await live()
                    except ToolRejected as exc:
                        result = ExecutionReceipt(
                            prepared.call_id, "rejected", "confirmed", reason_code=exc.code
                        )
                    except ModelError as exc:
                        result = ExecutionReceipt(
                            prepared.call_id,
                            "error",
                            "confirmed",
                            reason_code="source_unavailable" if exc.status >= 500 else exc.code,
                        )
                    await asyncio.to_thread(
                        self._settle,
                        context,
                        attempt_id,
                        effective_key,
                        result,
                        tool=prepared.tool.name,
                        reuse=read,
                    )
                    if (
                        not read
                        or result.reason_code not in TRANSIENT
                        or result.cleanup not in {"confirmed", "not_started"}
                        or attempt
                    ):
                        break
                    delay = (
                        result.payload.get("retryAfterSeconds", 0)
                        if isinstance(result.payload, dict)
                        else 0
                    )
                    if (
                        not isinstance(delay, (float, int))
                        or not 0 <= delay < context.control.remaining
                    ):
                        break
                    await asyncio.sleep(delay)
                    context.require_running()
                assert result is not None
                return {"receipt": asdict(result), "charges": charges}

        # The outer accepted receipt encloses any existing journaled SQL/semantic operation.
        value = await self.journal.call(
            "tool", identity, {"key": key}, dispatch, dict, revalidate=check, yield_boundary=False
        )
        receipt_value = dict(value["receipt"])
        if isinstance(payload := receipt_value.get("payload"), dict) and "callId" in payload:
            receipt_value["payload"] = {**payload, "callId": prepared.call_id}
        receipt = ExecutionReceipt(**receipt_value)
        if identity not in self.restored:
            if not executed:
                self.budget.tools += value["charges"]
                if value["charges"]:
                    context.on_start(prepared)
                if (
                    self.restore_plan
                    and prepared.tool.name == "todo_write"
                    and receipt.status == "ok"
                ):
                    self.restore_plan(prepared, context, receipt)
            self.restored.add(identity)
        return receipt


def remaining(budget):
    return {
        key: max(0, getattr(budget.limits, field) - getattr(budget, field))
        for key, field in [
            ("steps", "steps"),
            ("toolCalls", "tools"),
            ("tokens", "tokens"),
            ("costMicrousd", "cost_microusd"),
        ]
    }


def delivery_reserve(budget, route, seconds):
    """Keep one affordable generation after the current decision."""
    from compass_agent.context_runtime.pipeline import FRAME_BYTE_LIMIT

    if budget.steps + 1 >= budget.limits.steps or seconds <= 25:
        return True
    matching = [
        p
        for p in budget.profiles
        if p.provider_id == route.provider_id and p.binding_version == route.binding_version
    ]
    if len(matching) != 1:
        return False
    profile = matching[0]
    output = min(8192, profile.output_tokens)
    # The hard admission check reserves the entire provider window for delivery.
    # An intervening successful search can only consume the frame allowed by fit().
    inputs = profile.input_tokens + min(FRAME_BYTE_LIMIT, profile.input_tokens)
    tokens = inputs + 2 * output
    cost = inputs * profile.input_microusd + 2 * output * profile.output_microusd
    return (
        budget.limits.tokens - budget.tokens < tokens
        or budget.limits.cost_microusd - budget.cost_microusd < cost
    )


def result_kind(call, payload):
    if call.function.name in {"search_documents", "read_document"}:
        if isinstance(payload, dict) and (sections := payload.get("sections")):
            kinds = {section.get("kind") for section in sections}
            if len(kinds) == 1:
                return kinds.pop()
        try:
            return json.loads(call.function.arguments).get("kind") or "document"
        except ValueError:
            return "document"
    return "detail" if call.function.name == "database_query" else "semantic"


def fact_value(value):
    if isinstance(value, dict):
        return {
            k: fact_value(v)
            for k, v in value.items()
            if k
            not in {
                "observationId",
                "callId",
                "toolCallId",
                "resultId",
                "semantic_result_ref",
                "elapsedMs",
                "evidenceId",
                "query_ref",
            }
        }
    if isinstance(value, list):
        return [fact_value(v) for v in value]
    return value


class SearchProgress:
    def __init__(self, pin):
        self.pin = pin
        self.targets: dict[str, Target] = {}
        self.seen = set()
        self.stalled: dict[str, int] = {}
        self.batches = {}
        self.evidence = {}
        self.feedback = {}
        self.stop_reason: str | None = None
        self.last_gap: tuple[str, int] | None = None
        self.previous_draft = None
        self.claim_feedback = {}
        self.verified_partial = None
        self.repair_attempts: set[tuple[str, int]] = set()
        self.format_repaired = False
        self.plan_only_repeats = 0

    def plan(self, targets):
        updated = {t.key: t for t in targets}
        preserved = not any(
            target.required and updated.get(key) != target for key, target in self.targets.items()
        )
        self.targets = {
            **updated,
            **{key: t for key, t in self.targets.items() if t.required},
        }
        return preserved

    def independent_detail(self):
        return "detail" in self.pin["kinds"] and any(
            t.source == "detail" for t in self.targets.values()
        )

    def record(self, step, results, observations, budget):
        key = str(step.id)
        if key in self.batches:
            return
        changed, touched, gained, failed = [], set(), set(), set()
        for call, result in zip(step.tool_calls, results, strict=True):
            if call.id != result.call_id:
                raise ValueError("search_result_mismatch")
            if call.function.name not in READ_TOOLS:
                continue
            source = result_kind(call, result.payload)
            branches = {
                t.key
                for t in self.targets.values()
                if t.source == source
                or (source == "document" and t.source in {"history", "knowledge", "external"})
            } or {source}
            touched.update(branches)
            value = result.payload
            fingerprint = digest([source, fact_value(value)])
            if result.status == "ok" and value and fingerprint not in self.seen:
                self.seen.add(fingerprint)
                gained.update(branches)
                changed.append(call.id)
            elif result.status in {"rejected", "error"}:
                failed.update(branches)
        for branch in touched:
            self.stalled[branch] = 0 if branch in gained else self.stalled.get(branch, 0) + 1
        for observation in observations:
            self.evidence[observation["evidenceId"]] = observation
        viable = {
            t.key for t in self.targets.values() if t.required and t.source in self.pin["kinds"]
        }
        if not viable and not self.targets:
            viable = set(self.stalled)
        if remaining(budget)["toolCalls"] <= 0:
            self.stop_reason = "budget_exhausted"
        elif self.targets and not viable:
            self.stop_reason = "blocked"
        elif viable and all(self.stalled.get(k, 0) >= 2 for k in viable):
            self.stop_reason = "blocked" if viable.issubset(failed) else "no_progress"
        self.feedback = {
            "newFacts": [v for v in observations if v.get("callId") in changed],
            "changedCalls": changed,
            "openGaps": [t.model_dump() for t in self.targets.values()],
            "noGainBatches": dict(self.stalled),
            "remaining": remaining(budget),
            "stopReason": self.stop_reason or "continue",
        }
        self.batches[key] = self.feedback

    def snapshot(self):
        return {
            "targets": [t.model_dump() for t in self.targets.values()],
            "evidence": list(self.evidence.values()),
            "lastBatch": self.feedback,
            "stopReason": self.stop_reason,
        }


def model_observation(name, payload):
    """Keep usable business values, identifiers and coverage without transport envelopes."""
    if not isinstance(payload, dict):
        return payload
    if name in {"search_documents", "read_document"} and "sections" in payload:
        fields = (
            "kind",
            "docId",
            "title",
            "heading",
            "issuer",
            "ref",
            "version",
            "validFrom",
            "validTo",
            "sectionId",
            "text",
            "snippet",
        )
        return {
            "status": payload.get("status"),
            "truncated": payload.get("truncated"),
            "sections": [{k: s[k] for k in fields if k in s} for s in payload["sections"]],
        }
    if name == "query_metric" and payload.get("rows"):
        columns = payload["columns"]
        ok = "status" in columns and all(
            r[columns.index("status")] == "OK" for r in payload["rows"]
        )
        keep = [
            i
            for i, col in enumerate(columns)
            if col not in {"as_of", "data_release", "business_timezone", "unit"}
            and (col != "detail" or not ok)
        ]
        return {
            **{
                k: payload[k]
                for k in ("metric", "unit", "time_range", "period_totals")
                if k in payload
            },
            "columns": [columns[i] for i in keep],
            "rows": [[row[i] for i in keep] for row in payload["rows"]],
        }
    return payload


def search_messages(progress, state, question, phase, budget, correction):
    """Reopen this Run's admitted observations; retain the newest native batch intact."""
    from compass_agent.schemas import RUN_STREAM_LIMITS, ChatMessage

    batches = [step for step in state.steps if step.tool_results]
    latest = batches[-1] if batches else None
    catalog = list(progress.evidence.values())
    previous = []
    seen = set()
    for step in reversed(batches):
        if step is latest and phase != "generate":
            continue
        for call, result in zip(step.tool_calls, step.tool_results, strict=True):
            payload = json.loads(result.observation_json)
            key = digest([call.function.name, fact_value(payload)])
            if key in seen:
                continue
            seen.add(key)
            records = [v for v in catalog if v["callId"] == call.id]
            if phase == "generate" and not any(
                v["kind"] not in {"hint", "candidate"} for v in records
            ):
                continue
            if phase != "generate" and not records and len(previous) >= 3:
                continue
            previous.append(
                {
                    "call": call,
                    "evidence": records,
                    "observation": json.dumps(
                        model_observation(call.function.name, payload),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            )

    def data_message(content):
        return ChatMessage(
            role="user", content=json.dumps(content, ensure_ascii=False, separators=(",", ":"))
        )

    # Old observations can be compacted individually. The question, decision state and
    # the entire most recent native tool batch form one required, inseparable turn.
    messages = []
    for item in reversed(previous):
        call = item["call"]
        # Retrieved passages keep the tool role when reopened, including after compaction.
        messages.extend(
            [
                data_message(
                    {
                        "evidenceIds": [
                            e.get("sourceKey", e["evidenceId"])
                            for e in item["evidence"]
                            if e["kind"] not in {"candidate", "hint"}
                        ]
                    }
                ),
                ChatMessage(role="assistant", tool_calls=[call]),
                ChatMessage(role="tool", tool_call_id=call.id, content=item["observation"]),
            ]
        )
    material = {
        "question": question,
        "workspace": {
            "knownAt": progress.pin["knownAt"],
            "timezone": progress.pin["timezone"],
            "sourceKinds": progress.pin["kinds"],
        },
        "acceptedTargets": [t.model_dump() for t in progress.targets.values()],
        "claimFeedback": progress.claim_feedback,
        "remaining": remaining(budget),
        "maxCallsPerBatch": RUN_STREAM_LIMITS["toolsPerStep"],
        "searchFeedback": {
            k: v for k, v in progress.feedback.items() if k not in {"openGaps", "newFacts"}
        },
        "permittedReferences": [
            {
                k: v.get("sourceKey", v[k]) if k == "evidenceId" else v[k]
                for k in (
                    "evidenceId",
                    "callId",
                    "kind",
                    "title",
                    "documentRef",
                    "columns",
                    "columnUnits",
                    "rowCount",
                    "truncated",
                )
                if k in v
            }
            for v in catalog
            if v["kind"] not in {"candidate", "hint"}
        ],
    }
    if progress.pin.get("reportFormat") == "comparison_brief":
        material["delivery"] = (
            "经营简报按 comparisons 和 notes 交付。比较项要取得查询返回的基期和本期原值，"
            "从 permittedReferences 选择 evidenceId、从零起的行号和可用列名。"
            "贡献表和环比由程序计算。重要判断和行动须能指回已读原段或实际查询行。"
            "取得材料后直接交付，不必提交 claimProposals。"
        )
    if correction:
        material["correction"] = correction
    if progress.previous_draft is not None:
        material["previousDraft"] = {
            "instruction": (
                '已有私有草稿。补齐来源后可提交 {"revise":true} 重验。'
                "claimProposals 可替换或新增指定键，remove 可删除废弃键，"
                "answerBlocks 可调整正文。也可提交完整草稿。每次交付重验所有断言。"
            ),
            "claimKeys": [p.key for p in progress.previous_draft.claim_proposals],
            "answerBlocks": progress.previous_draft.answer_blocks,
        }
    if progress.stop_reason:
        material["stopReason"] = progress.stop_reason
    messages.append(data_message(material))
    if latest and phase != "generate":
        messages.append(ChatMessage(role="assistant", tool_calls=list(latest.tool_calls)))
        for call, result in zip(latest.tool_calls, latest.tool_results, strict=True):
            observation = model_observation(call.function.name, json.loads(result.observation_json))
            messages.append(
                ChatMessage(
                    role="tool",
                    tool_call_id=call.id,
                    content=json.dumps(observation, ensure_ascii=False, separators=(",", ":")),
                )
            )
    return messages
