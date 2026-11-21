"""Loopback OpenAI protocol fixture for the four-task browser acceptance test."""

import asyncio
import json
import re
from time import time
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI()
routing_gates: dict[UUID, asyncio.Event] = {}
QUERY = (
    "SELECT region_id, sum(amount)::int AS amount FROM core.channel_product_daily "
    "GROUP BY region_id ORDER BY region_id"
)


def observation(value):
    if isinstance(value, str):
        try:
            nested = json.loads(value)
        except ValueError:
            return None
        return observation(nested)
    if isinstance(value, list):
        return next((found for item in reversed(value) if (found := observation(item))), None)
    if isinstance(value, dict):
        if value.get("resultId") and "rows" in value:
            return value
        return observation(list(value.values()))
    return None


def fixture_message(body) -> dict[str, Any]:
    messages = body["messages"]
    system = "\n".join(m.get("content") or "" for m in messages if m["role"] == "system")
    user = "\n".join(str(m.get("content") or "") for m in messages if m["role"] == "user")
    tools = {t["function"]["name"] for t in body.get("tools", [])}
    record = observation(messages)
    answer = "当前缺少可读取的区域销售数据。"
    if "classify" in system.lower() or "scene_id" in system and "deliverable" in system:
        key, scene = next(
            (
                (k, s)
                for word, k, s in (
                    ("经营看板", "dashboard", "SC-25"),
                    ("经营报告", "business_report", "SC-29"),
                    ("数据分析", "data_analysis", "SC-02"),
                    ("经营问答", "business_qa", "SC-01"),
                )
                if word in user
            ),
            ("business_qa", "SC-01"),
        )
        answer = json.dumps({"scene_id": scene, "deliverable": key})
    elif "校验在线发布" in user:
        marker = re.search(r"(?m)^发布验收标识：(L21-[0-9a-f-]{36})$", system)
        answer = "本轮系统版本 " + (marker.group(1) if marker else "初始版本")
    elif "database_query" in tools and record is None and not body.get("response_format"):
        query = QUERY
        if "经营报告" in user:
            query = query.replace(" AS amount ", " AS amount, sum(cost)::int AS cost ")
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_retail_query",
                    "type": "function",
                    "function": {
                        "name": "database_query",
                        "arguments": json.dumps({"op": "query", "sql": query}),
                    },
                }
            ],
        }
    elif record and record.get("rows"):
        pairs = record["rows"]
        cols = record["columns"]
        ix, iy = cols.index("region_id"), cols.index("amount")
        summary = "按区域汇总销售额，" + "，".join(f"区域{r[ix]}为{r[iy]}" for r in pairs)
        summary += f"，合计{sum(int(r[iy]) for r in pairs)}。"
        if body.get("response_format") or re.search(r'"factRef"\s*:', system):
            answer = json.dumps(
                {
                    "title": "区域销售额对比",
                    "summary": summary,
                    "body": "## 区域分布\n\n" + summary,
                    "scope": "当前工作区授权可见的区域",
                    "views": [
                        {
                            "kind": "bar",
                            "title": "分区域销售额",
                            "factRef": record["resultId"],
                            "columns": cols,
                        }
                    ],
                },
                ensure_ascii=False,
            )
        else:
            answer = summary
    elif "标题" in system or "title" in system.lower():
        answer = "区域销售对比"
    print(
        "fixture",
        body["model"],
        "tools",
        ",".join(sorted(tools)),
        "source",
        bool(record),
        "schema",
        bool(body.get("response_format")),
        flush=True,
    )
    return {"role": "assistant", "content": answer}


@app.get("/gates/{id}")
def gate_state(id: UUID):
    return {"waiting": id in routing_gates}


@app.post("/gates/{id}/release")
async def release_gate(id: UUID):
    if event := routing_gates.get(id):
        event.set()
    return {"released": event is not None}


@app.post("/{path:path}")
async def completion(request: Request):
    body = await request.json()
    message = fixture_message(body)
    if re.search(r'"deliverable"\s*:', message.get("content", "")):
        for row in body["messages"]:
            match = re.search(r"发布门闩 ([a-f0-9-]{36})", row.get("content") or "")
            if match:
                key = UUID(match.group(1))
                event = routing_gates[key] = asyncio.Event()
                try:
                    await asyncio.wait_for(event.wait(), timeout=20)
                finally:
                    routing_gates.pop(key, None)
                break
    reason = "tool_calls" if message.get("tool_calls") else "stop"
    common = {"id": "lesson21-fixture", "model": body["model"], "created": int(time())}
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    if body.get("thinking", {}).get("type") == "enabled":
        message["reasoning_content"] = "核对可见的查询结果。"
    if not body.get("stream"):
        return {
            **common,
            "object": "chat.completion",
            "usage": usage,
            "choices": [{"index": 0, "message": message, "finish_reason": reason}],
        }
    if reason == "tool_calls":
        message["tool_calls"][0]["index"] = 0

    async def chunks():
        for delta, finish in ((message, None), ({}, reason)):
            chunk: dict[str, Any] = {
                **common,
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            if finish:
                chunk["usage"] = usage
            yield "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(chunks(), media_type="text/event-stream")
