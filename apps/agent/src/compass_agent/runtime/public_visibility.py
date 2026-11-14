"""Deterministic tool summaries and current-view filtering shared by snapshot and SSE."""

from copy import deepcopy

from compass_agent.schemas import AgentResponse

SUMMARY = {"policy": "summary", "inputPreview": None, "outputPreview": None, "language": "text"}


def public_event(event, *, details=True):
    value = deepcopy(event)
    if value["type"] != "view.upsert":
        return value
    obj = value["payload"]
    if obj["kind"] == "execution.node":
        node = obj["value"]
        # Legacy preview strings predate the authorized execution detail projection.
        node["display"] = dict(SUMMARY)
        if not details:
            node.pop("detail", None)
            if (node.get("origin") or {}).get("kind") == "mcp":
                node["origin"] = None
        if node.get("executor"):
            node["executor"]["environmentId"] = None
        if node["kind"] == "tool" and not details:
            node["title"] = {
                "retail_sales": "比较渠道实付金额",
                "database_query": "查询经营数据",
                "python_analysis": "生成分析图表",
            }.get((node.get("origin") or {}).get("toolName", ""), "工具调用")
            node["summary"] = {
                "completed": "工具已完成。",
                "empty": "未查询到结果。",
                "failed": "工具未完成。",
                "waiting": "等待执行确认。",
                "canceled": "调用已终止。",
                "incomplete": "结果待确认。",
            }.get(node["status"], "正在执行工具。")
    elif obj["kind"] == "timeline":
        progress = obj["value"]["event"]
        payload = progress["payload"]
        if progress["type"] == "tool.started":
            payload["inputPreview"] = None
            if not details:
                payload["params"] = None
        elif progress["type"] == "tool.finished":
            payload["resultPreview"] = None
            if not details:
                obj["value"]["event"] = {
                    "type": "stream.extension",
                    "payload": {
                        "name": "compass.runtime.tool_summary",
                        "version": 1,
                        "required": False,
                        "data": {"status": payload["status"], "summary": "工具调用状态已更新。"},
                    },
                }
    return value


def visible_response(response, *, details):
    value = response.model_dump(mode="json", by_alias=True)
    for packet in value["packets"]:
        packet["event"] = public_event(packet["event"], details=details)
    return AgentResponse.model_validate(value)
