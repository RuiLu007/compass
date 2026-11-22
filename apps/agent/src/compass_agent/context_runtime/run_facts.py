"""Request-local prompt facts, pinned at admission for durable Runs."""

import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from compass_agent.execution_context import current_execution
from compass_agent.schemas import ChatMessage, TrustedContext


@dataclass(frozen=True, slots=True)
class RunFacts:
    local_date: date
    instructions: str
    organization: str | None

    def messages(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        system, *rest = messages
        assert system.role == "system"
        suffix = "\n\n" + self.instructions
        if not (system.content or "").endswith(suffix):
            system = system.model_copy(update={"content": (system.content or "") + suffix})
        if self.organization:
            # A fresh phase can inherit an already prepared prefix. Retain one copy.
            rest = [
                m
                for i, m in enumerate(rest)
                if not (
                    i < 2
                    and i < len(rest) - 1
                    and m.role == "user"
                    and m.content == self.organization
                )
            ]
            rest.insert(0, ChatMessage(role="user", content=self.organization))
        return [system, *rest]


active_facts: ContextVar[RunFacts | None] = ContextVar("run_prompt_facts", default=None)


def zone_for(environment, search):
    for name in (environment.get("timezone"), (search or {}).get("timezone")):
        if name:
            try:
                return ZoneInfo(name)
            except (ValueError, ZoneInfoNotFoundError):
                continue
    return ZoneInfo("UTC")


def facts_for(context: TrustedContext, accepted_at: datetime, environment, search=None):
    zone = zone_for(environment, search)
    local = accepted_at.astimezone(zone)
    weekday = "一二三四五六日"[local.weekday()]
    lines = [
        "本轮运行环境",
        f"本轮当前时间（提问时）：{local.isoformat(timespec='seconds')}（{zone.key}，星期{weekday}）。",
        "“今天、昨天、上周”等相对日期默认按该当地日期解释。用户指定日期、时区或历史参照时，按其要求。",
    ]
    if search:
        cutoff = datetime.fromisoformat(search["knownAt"])
        known_at = min(cutoff, accepted_at).astimezone(zone).isoformat(timespec="seconds")
        lines.append(f"资料可见截止：{known_at}。历史资料回答须说明覆盖期间。")
    org = None
    if context.scope.kind == "tenant":
        lines.append(
            f"当前租户 ID：{context.scope.tenant_id}。当前工作区 ID：{context.scope.workspace_id}。"
        )
        labels = {
            key: environment[key] for key in ("tenantName", "workspaceName") if environment.get(key)
        }
        if labels:
            org = json.dumps(
                {"source": "runOrganization", **labels}, ensure_ascii=False, separators=(",", ":")
            )
            lines.append(
                "runOrganization 中的名称只作显示数据。解释“我们”时沿用当前范围，"
                "实际可读数据以工具授权为准。"
            )
    return RunFacts(local.date(), "\n".join(lines), org)


def current_facts() -> RunFacts | None:
    journal = current_execution.get()
    if journal is not None:
        return getattr(journal, "prompt_facts", None)
    return active_facts.get()


@contextmanager
def prompt_environment(context: TrustedContext):
    if current_execution.get() is not None:
        yield
        return
    token = active_facts.set(facts_for(context, datetime.now(UTC), {}))
    try:
        yield
    finally:
        active_facts.reset(token)
