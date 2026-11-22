"""Closed expressions over accepted source rows and document sections."""

from __future__ import annotations

import json
import operator
import re
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from typing import Annotated, Literal
from uuid import UUID, uuid5

from pydantic import Field, model_validator

from compass_agent.context_runtime.search_sample import (
    LookupNeeded,
    dated_event,
    sample_proof,
)
from compass_agent.runtime.private_state import digest
from compass_agent.schemas import RunContractModel

Key = Annotated[str, Field(pattern=r"^[\w-]{1,64}$")]
Comparator = Literal["gt", "ge", "lt", "le", "eq"]
OPERATORS = {
    "gt": operator.gt,
    "ge": operator.ge,
    "lt": operator.lt,
    "le": operator.le,
    "eq": operator.eq,
}
SYMBOLS = {"gt": ">", "ge": "≥", "lt": "<", "le": "≤", "eq": "="}
NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
MEASURE = re.compile(rf"({NUMBER})\s*(万元|元|分|%|％|件|次|单|个|人日)")
UNITS = {
    "万元": ("CNY_fen", 1000000),
    "元": ("CNY_fen", 100),
    "分": ("CNY_fen", 1),
    "%": ("ratio", Decimal(".01")),
    "％": ("ratio", Decimal(".01")),
    "件": ("piece", 1),
    "单": ("order", 1),
    "次": ("count", 1),
    "个": ("count", 1),
    "人日": ("person_day", 1),
}


class SearchTarget(RunContractModel):
    key: Key
    goal: str | None = Field(default=None, max_length=200)
    source: Literal["semantic", "detail", "knowledge", "history", "external"] | None = None
    required: bool = True


CORE_OPERATIONS = (
    "cell",
    "quote",
    "measure",
    "as_yuan",
    "multiply",
    "subtract",
    "sum",
    "compare",
    "test_hypothesis",
    "series",
    "gap",
)


class Proposal(RunContractModel):
    key: Key
    op: Key
    evidence_id: str | None = Field(default=None, alias="evidenceId", max_length=36)
    row: int | None = Field(default=None, ge=0, le=10000)
    column: str | None = Field(default=None, max_length=160)
    rows: list[int] | None = Field(default=None, max_length=16)
    columns: list[str] | None = Field(default=None, max_length=16)
    quote: str | None = Field(default=None, min_length=1, max_length=2048)
    parents: list[Key] = Field(default_factory=list, max_length=24)
    conflicts: list[Key] = Field(default_factory=list, max_length=8)
    comparator: Comparator | None = None
    unit: str | None = Field(default=None, max_length=32)
    reason: (
        Literal[
            "source_unavailable",
            "missing_receipt",
            "no_sample",
            "coverage",
            "authorization",
            "attribution",
            "eligibility",
        ]
        | None
    ) = None


class SearchDraft(RunContractModel):
    targets: list[SearchTarget] = Field(min_length=1, max_length=64)
    claim_proposals: list[Proposal] = Field(alias="claimProposals", min_length=1, max_length=96)
    answer_blocks: list[Key] = Field(alias="answerBlocks", min_length=1, max_length=96)

    @model_validator(mode="before")
    @classmethod
    def read_referenced_blocks(cls, value):
        if not isinstance(value, dict) or not isinstance(value.get("claimProposals"), list):
            return value
        claims = []
        for source in value["claimProposals"]:
            p = dict(source) if isinstance(source, dict) else {}
            op = p.get("op")
            if p.get("rows") is not None or p.get("columns") is not None:
                selected_rows = p.pop("rows", None)
                selected_columns = p.pop("columns", None)
                if not (
                    op == "series"
                    and not p.get("parents")
                    and p.get("evidenceId")
                    and isinstance(selected_rows, list)
                    and isinstance(selected_columns, list)
                    and 1 <= len(selected_rows) * len(selected_columns) <= 16
                    and all(type(i) is int for i in selected_rows)
                    and all(isinstance(c, str) for c in selected_columns)
                    and len(set(selected_rows)) == len(selected_rows)
                    and len(set(selected_columns)) == len(selected_columns)
                ):
                    raise ValueError("表格须指定本次回执、实际行号和列名，最多 16 个单元格")
                evidence = p.pop("evidenceId")
                parents = []
                for row in selected_rows:
                    for column in selected_columns:
                        key = f"_cell{len(claims)}"
                        claims.append(
                            dict(key=key, op="cell", evidenceId=evidence, row=row, column=column)
                        )
                        parents.append(key)
                p["parents"] = parents
            container = "quote" if op == "measure" else op
            if container in ("cell", "quote") and isinstance(p.get(container), dict):
                leaf_fields = p.pop(container)
                if set(p) & set(leaf_fields):
                    raise ValueError("同一观察字段重复")
                p.update(leaf_fields)
            if (
                op == "multiply"
                and p.get("factor") == 0.01
                and isinstance(p.get("parents"), list)
                and len(p["parents"]) == 1
            ):
                p.pop("factor")
                p["op"] = "as_yuan"
            claims.append(p)
        blocks = value.get("answerBlocks")
        if isinstance(blocks, list) and blocks and all(isinstance(b, dict) for b in blocks):
            keys = {c.get("key") for c in claims if isinstance(c.get("key"), str)}
            order = []
            for b in blocks:
                if set(b) - {"key", "claimKeys", "text"} or not isinstance(
                    b.get("claimKeys"), list
                ):
                    raise ValueError("正文段落须关联已核断言")
                if isinstance(b.get("key"), str) and b["key"] in keys:
                    order.append(b["key"])
                else:
                    order.extend(b["claimKeys"])
            if any(not isinstance(k, str) for k in order):
                raise ValueError("正文须使用本轮断言键")
            blocks = list(dict.fromkeys(order))
        return {**value, "claimProposals": claims, "answerBlocks": blocks}


class DraftRevision(RunContractModel):
    """Changes to the last private draft. Its complete dependency graph is rechecked."""

    revise: Literal[True]
    targets: list[SearchTarget] | None = Field(default=None, min_length=1, max_length=64)
    claim_proposals: list[Proposal] = Field(
        default_factory=list, alias="claimProposals", max_length=96
    )
    answer_blocks: list[Key] | None = Field(
        default=None, alias="answerBlocks", min_length=1, max_length=96
    )
    remove: list[Key] = Field(default_factory=list, max_length=96)

    def apply(self, previous: SearchDraft) -> SearchDraft:
        edits = {p.key: p for p in self.claim_proposals}
        if len(edits) != len(self.claim_proposals) or set(self.remove) & edits.keys():
            raise ValueError("请为每条待调整断言选择一次替换或移除")
        claims = {p.key: p for p in previous.claim_proposals if p.key not in self.remove}
        claims.update(edits)
        if len(claims) > 96:
            raise ValueError("核验草稿最多包含 96 项断言")
        ordered = {}
        visiting = set()

        def visit(key):
            if key in ordered:
                return
            if key in visiting or key not in claims:
                raise ValueError(f"请核对断言依赖 {key}，所有父项须存在且无循环")
            visiting.add(key)
            proposal = claims[key]
            for parent in [*proposal.parents, *proposal.conflicts]:
                visit(parent)
            visiting.remove(key)
            ordered[key] = proposal

        for key in claims:
            visit(key)
        return SearchDraft.model_validate(
            {
                "targets": self.targets or previous.targets,
                "claimProposals": [
                    p.model_dump(by_alias=True, exclude_none=True) for p in ordered.values()
                ],
                "answerBlocks": self.answer_blocks or previous.answer_blocks,
            }
        )


GAPS = {
    "source_unavailable": "所需来源暂不可用，请补充可读取的原件。",
    "missing_receipt": "缺少所需的正式查询回执，结论待核。",
    "no_sample": "当前查询范围缺少样本。",
    "coverage": "所需期间的数据尚未齐备。",
    "authorization": "当前权限下材料不足，请由有权负责人复核。",
    "attribution": "投放效果仍待归因核验。",
    "eligibility": "签批条件尚未核齐，暂缓执行。",
}

SOURCE_REQUIREMENT = {
    "semantic": "请指标负责人补齐该范围的正式指标回执。",
    "detail": "请业务数据负责人补充对应的明细记录与发生时间。",
    "knowledge": "请制度维护人提供适用条款。",
    "history": "请会议负责人补充当时的原始记录。",
    "external": "请对接人补充当前有效的外部原件。",
}


def target_gap(target, item=None):
    # A registered question can name what remains open; its wording supplies no facts.
    action = SOURCE_REQUIREMENT.get(target.source, GAPS["source_unavailable"])
    text = f"待核事项「{target.goal}」。{action}"
    if item and item.sources:
        return replace(item, text=f"{item.text} {text}")
    return Checked(text, (), "insufficient")


@dataclass(frozen=True)
class Checked:
    text: str
    sources: tuple[dict, ...]
    status: str = "supported"
    value: Decimal | None = None
    unit: str | None = None
    calculation: str | None = None
    coordinate: str | None = None
    condition: bool | None = None
    truncated: bool = False
    measure_source: str | None = None
    selection_balance: str | None = None
    current: bool = True
    rule_clauses: tuple[str, ...] = ()
    window: tuple[str, str] | None = None
    threshold: str | None = None
    subject: str | None = None
    comparison: tuple[Checked, str, Checked] | None = None
    policy_text: str | None = None
    field: str | None = None
    receipt_scope: tuple | None = None
    sample: dict | None = None
    event: dict | None = None
    lookup_problem: str | None = None
    authorized_source_tenant: int | str | None = None
    measure_label: str | None = None
    table: tuple[tuple[str, ...], ...] = ()
    detail_cell: tuple[str, int, str, str] | None = None


def number(value):
    try:
        n = Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        return None
    return n if n.is_finite() and abs(n.adjusted()) <= 40 else None


def display(n, unit):
    if unit == "CNY_fen":
        return f"{n / 100:,.2f} 元"
    if unit == "ratio":
        return f"{(n * 100).normalize():f}%"
    suffix = {"count": "次", "piece": "件", "order": "单", "person_day": "访客人日"}.get(
        unit, unit or ""
    )
    return f"{n.normalize():f} {suffix}".rstrip()


def merge_sources(items):
    merged = {digest(s): s for item in items for s in item.sources}
    if len(merged) > 64:
        raise ValueError("单项来源超限，请缩小查询范围")
    return tuple(merged.values())


_METRIC_SCOPE_COLUMNS = {
    "period_start",
    "period_end",
    "as_of",
    "group_key",
    "data_release",
    "business_timezone",
    "unit",
    "status",
    "detail",
}


def metric_period(record, cells):
    start, end = cells.get("period_start"), cells.get("period_end")
    windows = record.get("time_range") or []
    window = next((w for w in windows if w.get("start") == start), {})
    if not window and len(windows) == 1:
        window = windows[0]
    start, end = start or window.get("start"), end or window.get("end")
    key, group = str(cells.get("group_key", "")), window.get("group_by")
    if start and end and group in {"month", "day"} and key != "total":
        first = date.fromisoformat(key + "-01" if group == "month" else key)
        last = (
            (first.replace(day=28) + timedelta(days=4)).replace(day=1)
            if group == "month"
            else first + timedelta(days=1)
        )
        start, end = max(str(start), first.isoformat()), min(str(end), last.isoformat())
        if start >= end:
            raise ValueError("指标分组超出查询期间")
    return start, end, window


def metric_statement(evidence, cells, label, shown):
    record = evidence["record"]
    start, end, window = metric_period(record, cells)
    parts = []
    if start and end:
        last = date.fromisoformat(str(end)[:10]) - timedelta(days=1)
        parts.append(
            str(start) if str(start) == last.isoformat() else f"{start} 至 {last.isoformat()}"
        )
    sources = []

    dimensions = evidence.get("dimensionDisplay", {})

    def dimension(field, key):
        found = {}
        for definition in evidence.get("dictionaries", []):
            entry = definition["record"]
            entity = dimensions.get(field, {}).get("entity", "dimension." + field)
            if entry.get("kind") != "entity" or entry.get("code") != entity:
                continue
            # Only map the exact signed business key from the published entity definition.
            for name, value in re.findall(
                r"(?:^|[。；;\n])\s*([^=；;。\n]+?)\s*=\s*([0-9]+)(?=[；;。\n]|$)", entry["text"]
            ):
                if value == str(key):
                    source = {
                        k: definition[k] for k in ("kind", "title", "version", "scope", "locator")
                    }
                    found[name] = {**source, "excerpt": f"{name} = {value}"}
        if len(found) == 1:
            name, source = next(iter(found.items()))
            sources.append(source)
            return name
        return str(key)

    parts.extend(
        f"{dimensions.get(k, {}).get('label', k)} {dimension(k, v)}"
        for k, v in (window.get("filters") or {}).items()
    )
    if cells.get("group_key") not in (None, "total") and window.get("group_by") not in {
        "month",
        "day",
    }:
        group = window.get("group_by") or ""
        parts.append(
            " ".join(
                x
                for x in (
                    dimensions.get(group, {}).get("label", group),
                    dimension(group, cells["group_key"]),
                )
                if x
            )
        )
    parts.extend([label, shown])
    statement = " · ".join(parts)
    summary = (record.get("metric") or {}).get("summary")
    if summary and label == evidence["title"]:
        statement += "。" + summary
    return statement, sources


def leaf(p, evidence):
    if not evidence or evidence["kind"] in {"candidate", "hint"}:
        raise ValueError("请使用本轮已读原段或实际执行回执")
    kind, record = evidence["kind"], evidence["record"]
    source = {k: evidence[k] for k in ("kind", "title", "version", "scope", "locator")}
    if p.op == "cell":
        columns = record.get("columns", [])
        if (
            p.row is None
            or p.row >= len(record.get("rows", []))
            or columns.count(p.column) != 1
            or p.column not in evidence["available"]
            or kind not in {"metric", "detail"}
        ):
            raise ValueError("所选行列缺少可追溯的结果")
        cells = dict(zip(columns, record["rows"][p.row], strict=True))
        value = cells[p.column]
        unit = ""
        if kind == "metric" and p.column in {
            "value",
            "current_value",
            "comparison_value",
            "current_minus_comparison",
        }:
            unit = record["unit"]
        elif kind == "metric" and p.column in {"numerator", "denominator"}:
            unit = "count"
        elif kind == "detail":
            unit = evidence.get("units", {}).get(p.column, "")
        if p.unit is not None and p.unit != unit and UNITS.get(p.unit) != (unit, 1):
            expected = f"该列登记的单位为 {unit}" if unit else "该列未登记可换算的单位"
            raise ValueError(f"请求的单位与来源列不一致。{expected}，cell 可省略 unit 采用原值。")
        label = (
            evidence["title"]
            if kind == "metric" and p.column == "value"
            else evidence["available"][p.column]
        )
        title, label_source = evidence.get("displayColumns", {}).get(p.column, (label, None))
        row_keys = {
            k: v
            for k, v in cells.items()
            if k in {"period_start", "period_end", "group_key"}
            or k.endswith("_id")
            or (
                kind == "detail"
                and k != p.column
                and k in evidence["available"]
                and isinstance(v, str)
                and not evidence.get("units", {}).get(k)
            )
        }
        if kind == "detail":
            # A SQL CASE value names a computed partition. Keep its actual predicate
            # with that label so the analyst can see how the partition was defined.
            for key, v in tuple(row_keys.items()):
                expression = evidence["available"].get(key, "")
                if expression.startswith("CASE "):
                    row_keys[key] = f"{v}（{expression}）"
        scope = json.dumps(row_keys, ensure_ascii=False, sort_keys=True)
        locator = f"{source['locator']} · 行 {p.row + 1} · {p.column}"
        status = cells.get("status") if kind == "metric" else "OK"
        if status != "OK" or value is None:
            gap = GAPS["no_sample"] if status == "NO_FACTS" else GAPS["coverage"]
            if kind == "metric":
                gap, definitions = metric_statement(evidence, cells, evidence["title"], gap)
                locator = f"{source['locator']} · 行 {p.row + 1} · status"
            else:
                definitions = []
            return Checked(
                gap,
                ({**source, "locator": locator, "excerpt": str(status)}, *definitions),
                status="insufficient",
            )
        n = number(value)
        if unit == "CNY_yuan" and n is not None:
            n, unit = n * 100, "CNY_fen"
        shown = display(n, unit) if n is not None and unit else str(value)
        # The published statement retains the actual row scope. SQL aliases are never
        # accepted as the meaning of a column: available[] names its source expression.
        text = " · ".join(
            x
            for x in (
                title,
                " 至 ".join(
                    str(row_keys[k]) for k in ("period_start", "period_end") if k in row_keys
                ),
                str(row_keys.get("group_key", "")),
                "、".join(
                    str(v)
                    for k, v in row_keys.items()
                    if k not in {"period_start", "period_end", "group_key"}
                ),
                shown,
            )
            if x
        )
        definitions = []
        if kind == "metric":
            text, definitions = metric_statement(evidence, cells, label, shown)
        start, end, period = metric_period(record, cells)
        metric = record.get("metric") or {}
        sample, event, lookup_problem = None, None, None
        if kind == "detail":
            try:
                if unit == "count":
                    sample = sample_proof(evidence, cells, p.column)
                else:
                    event = dated_event(evidence, cells, p.column)
            except LookupNeeded as error:
                lookup_problem = str(error)
        coord = digest(
            [kind, evidence["title"], evidence["version"], evidence["scope"], scope, p.column]
        )
        return Checked(
            text,
            (
                {**source, "locator": locator, "excerpt": f"{scope} · {label} = {shown}"},
                *((label_source,) if label_source else ()),
                *definitions,
                *(sample["sources"] if sample else ()),
                *(event["sources"] if event else ()),
            ),
            value=n,
            unit=unit,
            coordinate=coord,
            truncated=bool(record.get("truncated")),
            window=(str(start), str(end)) if start and end else None,
            field=p.column,
            sample=sample,
            event=event,
            lookup_problem=lookup_problem,
            authorized_source_tenant=(record.get("data_scope") or {}).get("tenant_id")
            if kind == "metric"
            else None,
            measure_label=title,
            detail_cell=(evidence["locator"], p.row, title, shown) if kind == "detail" else None,
            receipt_scope=(
                tuple(sorted((period.get("filters") or {}).items())),
                period.get("group_by"),
                None if period.get("group_by") in {"day", "month"} else cells.get("group_key"),
                metric.get("asset_id") or metric.get("code") or evidence["title"],
            )
            if kind == "metric"
            else None,
        )
    if kind not in {"knowledge", "history", "external", "definition"} or (
        kind == "definition" and p.op != "quote"
    ):
        raise ValueError("条款需要已读原段")
    text = record["text"]
    if p.quote is None or text.count(p.quote) != 1:
        raise ValueError("引文须在所选原段唯一定位")
    start = text.index(p.quote)
    end = start + len(p.quote)
    # Render surrounding original text so an isolated clause cannot invert a negation.
    left = max(text.rfind(char, 0, start) for char in ("。", "！", "？", "\n")) + 1
    right = next((i + 1 for i in range(end, len(text)) if text[i] in "。！？\n"), len(text))
    if p.quote[-1] in "。！？\n":
        right = end
    sentence = text[left:right].strip()
    source.update(locator=f"{source['locator']} · [{start},{end})", excerpt=sentence)
    historical = not evidence["current"]
    presented = "历史版本原文记载“" + sentence + "”" if historical else sentence
    if p.op == "quote":
        return Checked(
            presented,
            (source,),
            current=evidence["current"],
            policy_text=text if kind == "knowledge" and evidence["current"] else None,
        )
    match = MEASURE.fullmatch(p.quote.strip())
    if not match or (start and (text[start - 1].isdigit() or text[start - 1] in ".,+-")):
        raise ValueError("数值须引用完整金额、比例或计数及原文单位")
    unit, scale = UNITS[match[2]]
    if p.unit is not None and p.unit not in {
        unit,
        match[2],
        "CNY_yuan" if match[2] == "元" else unit,
    }:
        raise ValueError("原文单位不一致")
    n = number(match[1])
    if n is None:
        raise ValueError("来源数值无效")
    clauses = tuple(re.findall(r"(?m)^\s*\d+[.、]\s*(.+)$", text)) if kind == "knowledge" else ()
    period = re.search(
        r"(20\d\d)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日至\s*(?:(\d{1,2})\s*月\s*)?(\d{1,2})\s*日",
        text,
    )
    window = None
    if period:
        y, m, day, em, ed = period.groups()
        window = (
            date(int(y), int(m), int(day)).isoformat(),
            (date(int(y), int(em or m), int(ed)) + timedelta(days=1)).isoformat(),
        )
    elif kind == "knowledge" and (
        month := re.search(r"按(?:照)?\s*(20\d\d)\s*年\s*(\d{1,2})\s*月", text)
    ):
        first = date(int(month[1]), int(month[2]), 1)
        window = (
            first.isoformat(),
            (first.replace(day=28) + timedelta(days=4)).replace(day=1).isoformat(),
        )
    before = sentence.partition(p.quote)[0].rstrip()
    threshold = next(
        (
            op
            for pattern, op in (
                (r"(?:至多|不(?:得)?超过|不高于|最多)$", "le"),
                (r"(?:未满|不到|未达到)$", "lt"),
                (r"(?:不少于|至少|不低于|达到|满)$", "ge"),
                (r"(?:超过|大于|高于)$", "gt"),
                (r"(?:低于|小于)$", "lt"),
            )
            if re.search(pattern, before)
        ),
        None,
    )
    if not threshold and kind == "knowledge" and re.search(r"门槛.{0,6}(?:为|定为)$", before):
        threshold = "ge"
    subject = None
    if kind == "knowledge" and re.search(r"仅(?:适用于)?该(?:地点|门店|渠道)", text):
        named = re.match(r"^([^，。；、]{2,48}?)(?:处在|处于|适用|执行)", text)
        if named:
            subject = named[1]
    cells = (
        [v.strip() for v in sentence.strip("|").split("|")]
        if sentence.startswith("|") and sentence.endswith("|")
        else []
    )
    row_label = None
    if len(cells) == 2 and cells.count(p.quote) == 1:
        row_label = cells[1 - cells.index(p.quote)]
    return Checked(
        presented,
        (source,),
        window=window,
        value=n * scale,
        unit=unit,
        current=evidence["current"],
        measure_source=digest([source["locator"], source["excerpt"]]),
        rule_clauses=clauses,
        threshold=threshold,
        subject=subject,
        measure_label=row_label,
    )


def series_table(values):
    # Reconstruct only complete rectangles from one signed result. SQL aliases never
    # become business labels, and a draft cannot move a value into another row.
    detail_cells = [v.detail_cell for v in values if v.status == "supported" and v.detail_cell]
    if (
        len(detail_cells) == len(values)
        and len(detail_cells) > 1
        and len({c[0] for c in detail_cells}) == 1
    ):
        headers = tuple(dict.fromkeys(c[2] for c in detail_cells))
        row_numbers = sorted({c[1] for c in detail_cells})
        data = {(row, label): value for _, row, label, value in detail_cells}
        if len(data) == len(detail_cells) == len(row_numbers) * len(headers):
            return (headers, *(tuple(data[row, label] for label in headers) for row in row_numbers))
    if len(values) < 2 or any(
        v.status != "supported" or v.value is None or not v.unit or not v.measure_label
        for v in values
    ):
        return ()
    first = values[0]
    if first.receipt_scope and all(
        v.window and v.receipt_scope and v.receipt_scope[:-1] == first.receipt_scope[:-1]
        for v in values
    ):
        labels = tuple(dict.fromkeys(v.measure_label for v in values))
        windows = sorted({v.window for v in values})
        cells = {(v.window, v.measure_label): v for v in values}
        if len(cells) == len(values) == len(windows) * len(labels):
            rows: list[tuple[str, ...]] = [("期间", *labels)]
            previous = {}
            for start, end in windows:
                period = f"{start} 至 {(date.fromisoformat(end) - timedelta(days=1)).isoformat()}"
                row = [period]
                for label in labels:
                    cell = cells[((start, end), label)]
                    shown = display(cell.value, cell.unit)
                    prior = previous.get(label)
                    if prior and prior.window[1] == start and prior.unit == cell.unit:
                        delta = cell.value - prior.value
                        if delta:
                            direction = "增加" if delta > 0 else "减少"
                            shown += f"（较上期{direction} {display(abs(delta), cell.unit)}）"
                        else:
                            shown += "（与上期持平）"
                    previous[label] = cell
                    row.append(shown)
                rows.append(tuple(row))
            return tuple(rows)
    rows = [("业务记录", "数值")]
    for v in values:
        shown = display(v.value, v.unit)
        label = v.text.rpartition(shown)[0].rstrip(" ·。") or v.measure_label
        rows.append((label, shown))
    return tuple(rows)


def evaluate(p, evidence, accepted, *, pack=None):
    verifier = None
    if p.op not in CORE_OPERATIONS:
        if not pack or p.op not in pack.get("proofOperations", ()):
            raise ValueError("本次运行未启用该核验操作")
        from compass_agent.documents.proof_packs import verifier_for

        verifier = verifier_for(pack["verifier"])

    if any(key not in accepted for key in [*p.parents, *p.conflicts]):
        raise ValueError("派生断言仅可使用本草稿中先前核实的父项")
    parents = [accepted[k] for k in p.parents]
    sources = merge_sources(parents)
    if p.op == "gap":
        if p.reason is None:
            raise ValueError("缺口须标明原因")
        scoped_gaps = [x.text for x in parents if x.status == "insufficient"]
        item = Checked(
            "\n".join(dict.fromkeys(scoped_gaps)) or GAPS[p.reason], sources, "insufficient"
        )
    elif p.op in {"quote", "measure", "cell"}:
        if parents:
            raise ValueError("来源叶子不得替换为派生断言")
        item = leaf(p, evidence)
    elif not parents or p.op != "series" and any(x.status != "supported" for x in parents):
        raise ValueError("必要父断言尚未核齐")
    elif p.op == "as_yuan":
        if (
            len(parents) != 1
            or parents[0].unit != "CNY_fen"
            or p.unit not in {None, "元", "CNY_yuan"}
        ):
            raise ValueError("金额换算须使用以分记账的已核金额")
        item = parents[0]
    elif p.op == "series":
        if len(parents) > 16:
            raise ValueError("事实组超过单项引用上限")
        statement = "\n".join(x.text for x in parents)
        if len(parents) > 1 and all(
            x.window
            and x.value is not None
            and x.unit
            and x.receipt_scope
            and x.receipt_scope == parents[0].receipt_scope
            and x.field == parents[0].field
            and x.unit == parents[0].unit
            for x in parents
        ):
            ordered = sorted(parents, key=lambda x: x.window)
            if all(
                prev.window[1] == cur.window[0]
                for prev, cur in zip(ordered, ordered[1:], strict=False)
            ):
                entries = []
                for i, current in enumerate(ordered):
                    part = f"{current.window[0]} 起 {display(current.value, current.unit)}"
                    if i:
                        diff = current.value - ordered[i - 1].value
                        change = "增加" if diff > 0 else "减少"
                        part += (
                            f"，较上期{change} {display(abs(diff), current.unit)}"
                            if diff
                            else "，与上期持平"
                        )
                    entries.append(part)
                statement = f"{parents[0].measure_label}。 " + "；".join(entries) + "。"
        group_status = next(
            (s for s in ("contested", "insufficient") if any(x.status == s for x in parents)),
            "supported",
        )
        item = Checked(statement, sources, group_status, table=series_table(parents))
    elif p.op == "test_hypothesis" and len(parents) > 2:
        first = parents[0]
        if (
            not p.comparator
            or not first.receipt_scope
            or not all(
                x.value is not None
                and x.unit == first.unit
                and x.field == first.field
                and x.receipt_scope == first.receipt_scope
                and x.window
                for x in parents
            )
            or not all(
                left.window[1] == right.window[0]
                for left, right in zip(parents, parents[1:], strict=False)
            )
        ):
            raise ValueError("连续趋势需要按顺序提交同范围、相邻期间的指标")
        passed = all(
            OPERATORS[p.comparator](left.value, right.value)
            for left, right in zip(parents, parents[1:], strict=False)
        )
        formula = f" {SYMBOLS[p.comparator]} ".join(display(x.value, x.unit) for x in parents)
        item = Checked(
            f"{first.measure_label}，自 {first.window[0]} 起逐期核对。{formula}，"
            + ("持续满足该比较条件。" if passed else "期间内出现反例。"),
            sources,
            status="supported" if passed else "refuted",
            calculation=formula,
            condition=passed,
        )
    elif verifier is not None:
        item = verifier(p, evidence, parents, sources)

    else:
        if any(x.value is None or not x.unit for x in parents):
            raise ValueError("计算需要有单位的已核数值")
        left = parents[0]
        if p.op == "sum":
            if any(x.truncated or x.unit != left.unit or x.unit == "ratio" for x in parents) or len(
                set(p.parents)
            ) != len(parents):
                raise ValueError("合计需要单位一致的独立可加数值")
            if len({x.coordinate for x in parents if x.coordinate}) != len(parents):
                raise ValueError("合计需要粒度可验证的不同回执行")
            value, unit = sum((x.value for x in parents), Decimal(0)), left.unit
            formula = " + ".join(display(x.value, x.unit) for x in parents)
        else:
            if len(parents) != 2:
                raise ValueError("该运算需要两个父项")
            right = parents[1]
            if (
                p.op == "multiply"
                and left.unit == "CNY_fen"
                and right.unit == "ratio"
                and 0 <= right.value <= 1
                and left.value >= 0
            ):
                value, unit = (
                    (left.value * right.value).to_integral_value(rounding=ROUND_FLOOR),
                    left.unit,
                )
                formula = f"{display(left.value, left.unit)} × {display(right.value, right.unit)}"
            elif p.op == "subtract" and left.unit == right.unit:
                if right.selection_balance and right.selection_balance != left.measure_source:
                    raise ValueError("余款须使用选档时核实的同一余额")
                value, unit = left.value - right.value, left.unit
                formula = f"{display(left.value, left.unit)} − {display(right.value, right.unit)}"
            elif (
                p.op in {"compare", "test_hypothesis"} and left.unit == right.unit and p.comparator
            ):
                if (
                    right.window
                    and any(s["kind"] == "knowledge" for s in right.sources)
                    and not (
                        left.window
                        and right.window[0] <= left.window[0]
                        and left.window[1] <= right.window[1]
                    )
                ):
                    raise ValueError("条款适用期未覆盖被比较的业务期间")
                if (
                    not right.current
                    and not right.window
                    and any(s["kind"] == "knowledge" for s in right.sources)
                ):
                    raise SearchGap("历史条款的适用期尚未与比较期间对齐，请核取适用原文。")
                if right.subject and not any(
                    s["kind"] == "definition"
                    and s["excerpt"].partition(" = ")[0].endswith(right.subject)
                    for s in left.sources
                ):
                    raise SearchGap(
                        "按该条款的适用对象查询已发布的业务键值域并核对分组：" + right.subject
                    )
                passed = OPERATORS[p.comparator](left.value, right.value)
                actual_relation = (
                    SYMBOLS[p.comparator]
                    if passed
                    else {"gt": "≤", "ge": "<", "lt": "≥", "le": ">", "eq": "≠"}[p.comparator]
                )
                formula = " ".join(
                    (
                        display(left.value, left.unit),
                        actual_relation,
                        display(right.value, right.unit),
                    )
                )
                decision = (
                    OPERATORS[right.threshold](left.value, right.value)
                    if right.threshold
                    else passed
                )
                statement = left.text.rstrip("。") + "。" + formula
                if right.threshold:
                    statement += (
                        "。" + right.text + (" 已达到该门槛。" if decision else " 未达到该门槛。")
                    )
                else:
                    statement = (
                        left.text.rstrip("。")
                        + "。与「"
                        + right.text.rstrip("。")
                        + "」比较，"
                        + formula
                    )
                    statement += "。"
                return Checked(
                    statement,
                    sources,
                    status="refuted" if p.op == "test_hypothesis" and not passed else "supported",
                    calculation=formula,
                    condition=decision,
                    comparison=(left, p.comparator, right),
                )
            else:
                raise ValueError("运算的角色、单位或比例不匹配")
        text = formula + " = " + display(value, unit)
        if p.op == "subtract" and all(x.measure_label for x in parents):
            labels = [
                f"{x.window[0]} 起的{x.measure_label}" if x.window else x.measure_label
                for x in parents
            ]
            text = f"{labels[0]}与{labels[1]}的差额为 {display(value, unit)}。"
        item = Checked(
            text,
            sources,
            value=value,
            unit=unit,
            calculation=formula + " = " + display(value, unit),
            current=all(x.current for x in parents),
        )
        if (
            p.op == "multiply"
            and {s["kind"] for s in left.sources} == {"history"}
            and {s["kind"] for s in parents[1].sources} == {"knowledge"}
        ):
            item = replace(
                item, selection_balance=left.measure_source, rule_clauses=parents[1].rule_clauses
            )
    if p.conflicts:
        contrary = [accepted[k] for k in p.conflicts]
        if not item.coordinate or not all(
            x.coordinate == item.coordinate and x.value != item.value and x.status == "supported"
            for x in contrary
        ):
            raise ValueError("反证须对齐相同对象、粒度、期间与单位")
        return replace(
            item,
            text="相同范围的来源取值有分歧，结论待核。",
            sources=merge_sources([item, *contrary]),
            status="contested",
            value=None,
        )
    return item


class SearchGap(ValueError):
    """An accepted goal still needs an admissible source or a terminal partial result."""


def render(
    run_id: UUID, draft: SearchDraft, records: dict, *, progress=None, requested=(), pack=None
):
    accepted = {}
    for p in draft.claim_proposals:
        if p.key in accepted:
            raise ValueError(f"断言键重复 {p.key}")
        try:
            evidence = records.get(p.evidence_id)
            if evidence and evidence["kind"] == "metric" and pack:
                evidence = {**evidence, "dimensionDisplay": pack.get("dimensionDisplay", {})}
            accepted[p.key] = evaluate(p, evidence, accepted, pack=pack)
        except ValueError as error:
            raise type(error)(f"断言 {p.key}（{p.op}）需修订。{error}") from error
    keys = draft.answer_blocks
    required = {t.key for t in draft.targets if t.required}
    if not required or not required <= set(keys) or any(k not in accepted for k in keys):
        raise ValueError("正文须覆盖所有必需目标并引用已核断言")
    # Detect contradictory values regardless of which one the draft elects to show.
    original = list(accepted.values())
    for key, item in tuple(accepted.items()):
        if item.coordinate and any(
            other.coordinate == item.coordinate
            and other.value != item.value
            and other.status == "supported"
            for other in original
        ):
            accepted[key] = replace(
                item,
                status="contested",
                text="相同范围的来源取值有分歧，结论待核。",
                value=None,
                sources=merge_sources([x for x in original if x.coordinate == item.coordinate]),
            )
    # A contested input invalidates each descendant, even when the model proposes
    # the contrary leaf after proposing a calculation.
    for proposal in draft.claim_proposals:
        inputs = [accepted[k] for k in proposal.parents]
        if proposal.op == "series" and any(x.status == "contested" for x in inputs):
            accepted[proposal.key] = evaluate(proposal, None, accepted)
        elif proposal.op not in {"series", "gap"} and any(
            x.status in {"refuted", "contested"} for x in inputs
        ):
            accepted[proposal.key] = Checked(
                "依据之间仍有分歧，相关结论待核。", merge_sources(inputs), "contested"
            )
    if progress:
        if (
            not progress.targets
            and progress.pin["kinds"]
            and not progress.stop_reason
            and all(accepted[k].status == "insufficient" and not accepted[k].sources for k in keys)
        ):
            raise SearchGap("仍有可查询的获准来源，请登记待核事项并检索所需材料。")
        proposed = {t.key: t for t in draft.targets}
        for target in progress.targets.values():
            if not target.required:
                continue
            declared = proposed.get(target.key)
            if (
                not declared
                or not declared.required
                or target.key not in keys
                or declared.source not in {None, target.source}
                or declared.goal not in {None, target.goal}
            ):
                raise SearchGap("请保留已登记的必需事项及其来源：" + target.key)
            item = accepted[target.key]
            kind = "metric" if target.source == "semantic" else target.source
            if item.status in {"supported", "refuted"} and kind not in {
                s["kind"] for s in item.sources
            }:
                raise SearchGap("该事项仍缺少指定来源的回执：" + target.key)
            if (
                item.status in {"insufficient", "contested"}
                and target.source in progress.pin["kinds"]
                and not progress.stop_reason
                and progress.stalled.get(target.key, 0) < 2
                and not any(
                    s["kind"] == "metric"
                    and s["excerpt"]
                    in {
                        "NO_FACTS",
                        "NO_SAMPLE",
                        "COVERAGE_UNKNOWN",
                        "SOURCE_DELAYED",
                        "OUTSIDE_SNAPSHOT_COVERAGE",
                    }
                    for s in item.sources
                )
            ):
                raise SearchGap("该事项仍有可查询的获准来源：" + target.key)
            if item.status == "insufficient" and (not item.sources or item.text in GAPS.values()):
                accepted[target.key] = target_gap(target, item)
    if requested:
        published = {
            s["locator"].split(" · 行 ", 1)[0]
            for key in keys
            for s in accepted[key].sources
            if s["kind"] == "metric"
        }
        found = {
            rec["record"]["metric"]["asset_id"]
            for rec in records.values()
            if rec and rec.get("kind") == "metric" and rec["locator"] in published
        }
        if {m["asset_id"] for m in requested} - found and not (progress and progress.stop_reason):
            raise SearchGap("已识别的正式指标仍缺少本次执行回执。")
    proposals = {p.key: p for p in draft.claim_proposals}
    measure_records = {
        p.evidence_id
        for k in keys
        if (p := proposals[k]).op == "cell"
        and p.column not in _METRIC_SCOPE_COLUMNS
        and p.evidence_id
        and records[p.evidence_id]["kind"] == "metric"
    }
    visible = []
    presented_gaps = set()
    for key in keys:
        proposal = proposals[key]
        if (
            proposal.op == "cell"
            and proposal.evidence_id in measure_records
            and proposal.column in _METRIC_SCOPE_COLUMNS
        ):
            continue
        item = accepted[key]
        if item.status == "insufficient":
            gap_key = digest([item.text, item.sources])
            if gap_key in presented_gaps:
                continue
            presented_gaps.add(gap_key)
        visible.append(item)
    body, manifest = format_checked(run_id, visible)
    manifest["decisionComplete"] = all(
        accepted[k].status in {"supported", "refuted"} for k in required
    )
    return body, manifest


def format_checked(run_id, items, *, byte_limit=None):
    body = ""
    encoded_size = 0
    anchors, details, numbered = [], {}, {}
    for item in items:
        statement = item.text

        # Plain-text statements escape Markdown punctuation before interpolation.
        def escape(value):
            return re.sub(r"([\\`*_{}\[\]()<>#!|~])", r"\\\1", value)

        if item.table:
            lines = [
                "| " + " | ".join(escape(cell).replace("\n", " ") for cell in row) + " |"
                for row in item.table
            ]
            lines.insert(1, "| " + " | ".join("---" for _ in item.table[0]) + " |")
            escaped = "\n".join(lines) + "\n\n"
        else:
            escaped = escape(statement)
        block = ("\n\n" if body else "") + escaped
        # Reserve space for the largest possible ordinal in this partial. Keep each
        # claim and its marker together, including when an earlier block was skipped.
        size = len(block.encode()) + (len(f"[{len(numbered) + 1}]") if item.sources else 0)
        if byte_limit is not None and encoded_size + size > byte_limit:
            continue
        encoded_size += size
        body += block
        if not item.sources:
            continue
        claim = str(
            uuid5(
                run_id,
                "claim:" + digest([item.status, statement, list(item.sources), item.calculation]),
            )
        )
        cid = str(uuid5(run_id, "citation:" + claim))
        ordinal = numbered.setdefault(cid, len(numbered) + 1)
        marker = f"[{ordinal}]"
        anchors.append(
            {
                "citationId": cid,
                "ordinal": ordinal,
                "start": len(body),
                "end": len(body) + len(marker),
            }
        )
        body += marker
        details[cid] = {
            "citationId": cid,
            "claimId": claim,
            "revision": 1,
            "status": item.status,
            "statement": statement,
            "calculation": item.calculation,
            "sources": list(item.sources),
        }
    return body, {"anchors": anchors, "details": details}
