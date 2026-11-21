"""Render the effective scope and period already verified by the read-only executor."""

import json
from decimal import Decimal
from itertools import combinations

from compass_agent.semantic.execution import exact_sum, observed_value


def scope_lines(observations):
    lines = {}
    definitions = {}
    for record in observations.values():
        metric = record.get("metric")
        if not metric:
            continue
        title = metric.get("name", "指标") + " v" + str(metric.get("version", ""))
        identity = (metric.get("asset_id"), metric.get("version_id"), title)
        if identity not in definitions:
            details = [
                label + metric[key]
                for key, label in (
                    ("summary", ""),
                    ("event_time", "业务时间 "),
                    ("known_at", "数据可见规则 "),
                    ("formula", "公式 "),
                    ("exceptions", "例外规则 "),
                )
                if isinstance(metric.get(key), str) and metric[key].strip()
            ]
            if details:
                definitions[identity] = " · ".join([title, *details])
        scope = record.get("data_scope") or {}
        if scope.get("all_regions"):
            audience = "租户 " + str(scope["tenant_id"]) + " 全部授权地区"
        elif scope.get("region_ids"):
            audience = (
                "租户 "
                + str(scope["tenant_id"])
                + " 地区 "
                + "、".join(map(str, scope["region_ids"]))
            )
        else:
            audience = "当前工作区的来源授权范围"
        totals = {(t["start"], t["end"]): t["value"] for t in record.get("period_totals", [])}
        for window in record.get("time_range", []):
            filters = json.dumps(window.get("filters") or {}, ensure_ascii=False, sort_keys=True)
            details = [
                title,
                f"{window['start']} 至 {window['end']}（结束日不含）",
                "截至 " + str(record.get("as_of", "")),
                audience,
                "单位 " + str(record.get("unit", "")),
                "分组 " + str(window.get("group_by") or "total"),
            ]
            total = totals.get((window["start"], window["end"]))
            if total is not None:
                details.append(f"分组合计 {total} {record['unit']}")
            if filters != "{}":
                details.append("筛选 " + filters)
            lines[" · ".join(details)] = None
    return [*lines, *definitions.values()]


def group_reconciliation_lines(observations):
    """Compare complete group sums with the separately queried total of the same binding."""
    by_window = {}
    for record in observations.values():
        metric = record.get("metric") or {}
        identity = (
            metric.get("asset_id"),
            metric.get("version_id"),
            record.get("binding_hash"),
            record.get("source_scope_hash"),
            record.get("manifest_hash"),
            record.get("as_of"),
            record.get("unit"),
        )
        if not all(identity) or not str(record.get("aggregation", "")).startswith("additive"):
            continue
        for window in record.get("time_range", []):
            start, end = window["start"], window["end"]
            axis = window.get("group_by", "total")
            key = (identity, start, end, json.dumps(window.get("filters") or {}, sort_keys=True))
            if axis == "total":
                values = set()
                for cells in record["rows"]:
                    row = dict(zip(record["columns"], cells, strict=True))
                    if (row.get("group_key"), row.get("period_start"), row.get("period_end")) == (
                        "total",
                        start,
                        end,
                    ):
                        value = observed_value(row)
                        if value is not None:
                            values.add(value)
            else:
                values = {
                    Decimal(item["value"])
                    for item in record.get("period_totals", [])
                    if (item["start"], item["end"]) == (start, end)
                }
            if values:
                axes = by_window.setdefault(key, (metric["name"], {}))[1]
                axes.setdefault(axis, set()).update(values)
    lines = []
    for (_, start, end, filters), (name, axes) in by_window.items():
        totals = axes.get("total", set())
        if len(totals) != 1:
            continue
        total = next(iter(totals))
        for axis, values in axes.items():
            if axis == "total" or len(values) != 1:
                continue
            value = next(iter(values))
            outcome = (
                "一致" if value == total else f"差额 {exact_sum([value, total.copy_negate()]):f}"
            )
            scope = "" if filters == "{}" else f" · 筛选 {filters}"
            lines.append(
                f"{name} {start} 至 {end}{scope} · {axis} 分组合计 {value:f}，"
                f"独立总计 {total:f}，核对结果{outcome}。"
            )
    return lines


def reconciliation_lines(observations, requested):
    """Compare requested additive totals only within the same authorized source and window."""
    targets = {m["asset_id"]: m["name"] for m in requested if "asset_id" in m and "name" in m}
    if not 2 <= len(targets) <= 4:
        return []
    groups = {}
    for record in observations.values():
        metric = record.get("metric", {}).get("asset_id")
        if metric not in targets or not str(record.get("aggregation", "")).startswith("additive"):
            continue
        source = record.get("source_scope_hash")
        if not source or not record.get("manifest_hash"):
            continue
        for window in record.get("time_range", []):
            if window.get("group_by", "total") != "total":
                continue
            for values in record.get("rows", []):
                row = dict(zip(record["columns"], values, strict=True))
                if (
                    row.get("group_key") != "total"
                    or row.get("period_start") != window["start"]
                    or row.get("period_end") != window["end"]
                    or (value := observed_value(row)) is None
                ):
                    continue
                key = (
                    window["start"],
                    window["end"],
                    record["unit"],
                    source,
                    record["manifest_hash"],
                    record.get("as_of"),
                    record["metric"].get("timezone"),
                    json.dumps(window.get("filters") or {}, sort_keys=True),
                )
                groups.setdefault(key, {})[metric] = value
    lines = []
    for (start, end, unit, *_), values in groups.items():
        for a, b in combinations((m for m in targets if m in values), 2):
            change = exact_sum([values[a], values[b].copy_negate()])
            lines.append(
                f"{start} 至 {end}（结束日不含） · {targets[a]} 减 {targets[b]} = {change:f} {unit}"
            )
    return lines


def with_scope(text, observations, limit, *, requested=()):
    lines = [
        *scope_lines(observations),
        *group_reconciliation_lines(observations),
        *reconciliation_lines(observations, requested),
    ]
    if lines:
        suffix = "\n\n取数口径\n\n" + "\n".join("- " + line for line in lines)
        if len((text + suffix).encode()) <= limit:
            return text + suffix
    return text
