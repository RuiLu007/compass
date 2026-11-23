"""Preserve query cells and published definitions as tool-owned evidence."""

from compass_agent.runtime.private_state import digest


def result(args, payload, refs):
    if not isinstance(payload, dict):
        return
    if "rows" in payload and "columns" in payload:
        record = payload
        if payload.get("metric") and payload.get("time_range"):
            record = {**payload, "time_bounds": {"start": "inclusive", "end": "exclusive"}}
        yield {
            "kind": "query",
            "title": (payload.get("metric") or {}).get("name", "查询结果"),
            "version": str(payload.get("connection_revision", payload.get("data_release", ""))),
            "locator": str(payload.get("resultId") or payload.get("queryId") or digest(payload)),
            "record": record,
            "sourceRefs": refs,
        }
    elif payload.get("version_id") and payload.get("name"):
        selected = [r for r in refs if r.get("version_id") == payload["version_id"]]
        if selected:
            text = "\n".join(
                payload[k]
                for k in ("summary", "formula", "event_time", "grain", "exceptions")
                if isinstance(payload.get(k), str) and payload[k]
            )
            yield {
                "kind": "definition",
                "title": payload["name"],
                "version": str(payload.get("version", "")),
                "locator": payload["version_id"],
                "record": {**payload, "text": text, "sectionId": payload["version_id"]},
                "sourceRefs": selected,
            }
