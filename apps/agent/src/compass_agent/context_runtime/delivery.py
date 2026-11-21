"""Validate a final delivery and materialize files from the accepted observations."""

import csv
import io
import json
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from hashlib import sha256
from typing import Literal
from uuid import uuid5

from pydantic import Field, ValidationError
from sqlalchemy import func, select

from compass_agent.execution_context import execution_path
from compass_agent.models.errors import ModelError
from compass_agent.runtime.entities import AgentArtifact, AgentStep
from compass_agent.schemas import (
    RUN_STREAM_LIMITS,
    AnalysisFileNone,
    DashboardDocumentV1,
    DashboardWidget,
    RunCompleted,
    RunContractModel,
)


class ViewSpec(RunContractModel):
    kind: Literal["metric", "table", "bar", "line"]
    title: str = Field(min_length=1, max_length=160)
    fact_ref: str = Field(
        alias="factRef", min_length=1, max_length=200, description="acceptedViews[].factRef"
    )
    columns: list[str] = Field(
        min_length=1, max_length=50, description="acceptedViews[factRef].columns"
    )


class DeliveryDraft(RunContractModel):
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=8000)
    body: str = Field(max_length=200000)
    scope: str = Field(min_length=1, max_length=500)
    views: list[ViewSpec] = Field(max_length=24)


@dataclass(frozen=True)
class Delivery:
    node_key: str
    slot: str
    name: str
    media: str
    data: bytes


def output_contract(task):
    return (
        DeliveryDraft.model_json_schema(by_alias=True)
        if task in {"data_analysis", "dashboard", "business_report"}
        else None
    )


def validation_feedback(error):
    if isinstance(error, ValidationError):
        return [
            {"field": list(item["loc"]), "issue": item["type"]}
            for item in error.errors(include_input=False, include_url=False)[:8]
        ]
    return str(error)[:200]


def query_observations(state):
    observations = {}
    for step in state.steps:
        for call, result in zip(step.tool_calls, step.tool_results, strict=False):
            if (
                call.function.name not in {"database_query", "query_metric"}
                or result.status != "ok"
            ):
                continue
            record = json.loads(result.observation_json)
            if not isinstance(record, dict) or not isinstance(record.get("resultId"), str):
                continue
            observations[record["resultId"]] = record
    return observations


def accepted_views(state):
    """Expose only result IDs and unambiguous column labels that can back a view."""
    choices = []
    for fact_ref, record in query_observations(state).items():
        labels = record.get("columns", [])
        columns = [col for col in labels if isinstance(col, str) and labels.count(col) == 1]
        if columns:
            choices.append({"factRef": fact_ref, "columns": columns})
    return choices


def _widgets(views, state):
    observations = query_observations(state)
    widgets = []
    for view in views:
        record = observations.get(view.fact_ref)
        if (
            record is None
            or len(set(view.columns)) != len(view.columns)
            or any(record["columns"].count(col) != 1 for col in view.columns)
        ):
            raise ValueError("unknown observation or columns")
        indices = [record["columns"].index(col) for col in view.columns]
        rows = [[row[i] for i in indices] for row in record["rows"]]
        widgets.append(
            DashboardWidget(
                kind=view.kind,
                title=view.title,
                factRef=view.fact_ref,
                columns=view.columns,
                rows=rows,
            )
        )
    return widgets


_NUMBER = r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"


def _numbers(values):
    numbers = []
    for value in values:
        if not isinstance(value, (str, float, int)) or isinstance(value, bool):
            continue
        try:
            number = Decimal(str(value).replace(",", ""))
        except InvalidOperation:
            continue
        if number.is_finite() and abs(number.adjusted()) <= 100:
            numbers.append(number)
    return numbers


# Explicit display conversions. A newly registered unit always supports its own label.
_DISPLAY_UNITS = {
    "CNY_fen": {"分": 1, "元": 100, "万元": 1_000_000, "亿元": 10_000_000_000},
    "order": {"单": 1},
    "piece": {"件": 1},
    "ratio": {"倍": 1, "%": Decimal("0.01")},
}


class UnverifiedMeasurement(ValueError):
    pass


def _check_measurements(draft, widgets, observations=None):
    observations = observations or {}
    formal = {}
    legacy = set()
    shares = set()
    comparable = {}
    # Formal answers retain every signed source from this Run, including metrics
    # explained in prose while a different result is shown in a chart.
    data = [(r["columns"], r["rows"], r) for r in observations.values() if r.get("metric")]
    data.extend(
        (widget.columns, widget.rows, {})
        for widget in widgets
        if not observations.get(widget.fact_ref, {}).get("metric")
    )
    if not data:
        return
    for visible_columns, visible_rows, result in data:
        if not result.get("metric"):
            for column in zip(*visible_rows, strict=True):
                nums = _numbers(column)
                legacy.update(nums)
                if len(nums) == len(column):
                    legacy.add(sum(nums))
            continue
        if "value" not in visible_columns:
            continue
        col = visible_columns.index("value")
        numbers = _numbers(row[col] for row in visible_rows)
        unit = result["unit"]
        readings = formal.setdefault(unit, set())
        readings.update(numbers)
        readings.update(_numbers(t["value"] for t in result.get("period_totals", [])))
        if "current_minus_comparison" in visible_columns:
            delta = visible_columns.index("current_minus_comparison")
            readings.update(_numbers(row[delta] for row in visible_rows))
        additive = str(result.get("aggregation", "")).startswith("additive")
        if additive and numbers and len(result.get("time_range", [])) <= 1:
            total = sum(numbers)
            readings.add(total)
            if total and len(numbers) > 1 and len(result.get("time_range", [])) == 1:
                shares.update(n * 100 / total for n in numbers)
        # Differences only across the same source snapshot, authorized scope, unit and group.
        columns = result.get("columns", [])
        if all(k in columns for k in ("group_key", "value", "status")):
            for row in result["rows"]:
                if row[columns.index("status")] != "OK":
                    continue
                values = _numbers([row[columns.index("value")]])
                if values:
                    scope = (
                        unit,
                        result.get("authorized_scope"),
                        result.get("source_scope_hash"),
                        json.dumps(result.get("data_scope", {}), sort_keys=True),
                        result.get("manifest_hash"),
                        result.get("as_of"),
                        row[columns.index("group_key")],
                        result.get("metric", {}).get("timezone"),
                    )
                    parameters = (result.get("time_range") or [{}])[0]
                    grouping = (
                        parameters.get("group_by", "total"),
                        json.dumps(parameters.get("filters", {}), sort_keys=True),
                    )
                    period = tuple(
                        row[columns.index(k)] if k in columns else parameters.get(p)
                        for k, p in (("period_start", "start"), ("period_end", "end"))
                    )
                    metric = result.get("metric", {}).get("asset_id")
                    if all(period):
                        comparable.setdefault((*scope, *grouping, "window", *period), set()).add(
                            values[0]
                        )
                    if metric:
                        comparable.setdefault((*scope, *grouping, "metric", metric), set()).add(
                            values[0]
                        )
    for (unit, *_), nums in comparable.items():
        if len(nums) <= 50:
            formal[unit].update(a - b for a in nums for b in nums)
    labels = {"亿元", "万元", "元", "单", "件", "笔", "人次", "%"}
    for unit in formal:
        labels.update([unit, *_DISPLAY_UNITS.get(unit, {})])
    pattern = re.compile(
        rf"({_NUMBER})\s*("
        + "|".join(re.escape(u) for u in sorted(labels, key=len, reverse=True))
        + ")"
    )
    for raw, label in pattern.findall(draft.summary + "\n" + draft.body):
        n = Decimal(raw.replace(",", ""))
        allowed = set()
        for unit, readings in formal.items():
            ratio = {unit: 1, **_DISPLAY_UNITS.get(unit, {})}.get(label)
            if ratio:
                allowed.update(v / ratio for v in readings)
        if label == "%":
            allowed.update(shares)
        # Business reporting may round an observed quantity to its displayed decimal place.
        exponent = n.as_tuple().exponent
        precision = Decimal(1).scaleb(exponent if isinstance(exponent, int) else 0)
        if n in allowed or any(
            isinstance(exponent, int)
            and v.adjusted() - exponent < 27
            and v.quantize(precision, rounding=ROUND_HALF_UP) == n
            for v in allowed
        ):
            continue
        legacy_scale = {"亿元": 100_000_000, "万元": 10_000, "%": Decimal("0.01")}.get(label, 1)
        if n not in legacy and n * legacy_scale not in legacy:
            raise UnverifiedMeasurement(
                "measurement missing from the referenced observations: " + raw + label
            )


def _csv_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _markdown_cell(value):
    if value is None:
        return ""
    text = str(value).replace("\n", " ").replace("\r", " ")
    return "".join("\\" + char if char in r"\|<>[]`#*_!" else char for char in text)


def verified_report(observations, requested):
    """Render all requested, signed query results when the narrative fails numerical review."""
    wanted = {m["asset_id"] for m in requested}
    covered, seen, views = set(), set(), []
    for ref, record in observations.items():
        metric = record.get("metric") or {}
        key = metric.get("asset_id")
        if key not in wanted:
            continue
        if not all(
            record.get(field) for field in ("binding_hash", "manifest_hash", "source_scope_hash")
        ):
            return None
        windows = record.get("time_range")
        columns = record.get("columns", [])
        if (
            not windows
            or not record.get("rows")
            or not {"group_key", "value", "status"} <= set(columns)
        ):
            return None
        scope = (
            key,
            record["binding_hash"],
            record["source_scope_hash"],
            json.dumps(windows, sort_keys=True),
            record["manifest_hash"],
        )
        if scope in seen:
            continue
        seen.add(scope)
        covered.add(key)
        facts = [
            c
            for c in (
                "period_start",
                "period_end",
                "group_key",
                "value",
                "unit",
                "status",
                "current_value",
                "comparison_value",
                "current_minus_comparison",
            )
            if c in columns
        ]
        views.append(ViewSpec(kind="table", title=metric["name"], factRef=ref, columns=facts))
    if not wanted or covered != wanted or not 1 <= len(views) <= 24:
        return None
    return DeliveryDraft(
        title="已核验指标报告",
        scope="本次正式查询的授权范围",
        summary="自动解读未通过校验。报告列出了本次查询的指标、期间和实际结果。",
        body="各表按已发布口径列示。空值的原因见对应行的状态，取数范围见表后说明。",
        views=views,
    )


def complete_delivery(task, text, state, step, journal, *, allow_verified_report=False):
    from compass_agent.semantic.receipts import with_scope

    observations = query_observations(state)
    initial = getattr(journal, "registry_initial_context", None) or {}
    requested = initial.get("requested_metrics", [])
    may_recover = allow_verified_report and task in {"business_report", "data_analysis"}
    draft = None
    widgets = []
    if text is not None:
        try:
            candidate = DeliveryDraft.model_validate_json(text)
            widgets = _widgets(candidate.views, state)
            _check_measurements(candidate, widgets, observations)
            draft = candidate
        except (UnverifiedMeasurement, ValidationError):
            if not may_recover:
                raise
    if draft is None:
        if not may_recover:
            raise ValueError("delivery requires valid model output")
        selected = getattr(journal, "registry_selected", None) or (
            initial.get("resolution") or {}
        ).get("metric")
        targets = requested or ([selected] if selected else [])
        recovered = verified_report(observations, targets)
        if recovered is None:
            raise ValueError("report requires formal results for every requested metric")
        draft, widgets = recovered, _widgets(recovered.views, state)
    summary = with_scope(
        draft.summary, observations, RUN_STREAM_LIMITS["answerBytes"], requested=requested
    )
    receipt = RunCompleted(
        status="completed", finalAnswer=summary, file=AnalysisFileNone(status="none")
    )
    node_key = f"{execution_path.get()}/model:{step.id}"
    if task == "dashboard":
        if not widgets:
            raise ValueError("a dashboard needs an accepted observation")
        doc = DashboardDocumentV1(
            schemaVersion=1,
            title=draft.title,
            scope=draft.scope,
            asOf=journal.context_date.isoformat(),
            widgets=widgets,
        )
        file = Delivery(
            node_key,
            "dashboard",
            "dashboard.json",
            "application/json",
            doc.model_dump_json(by_alias=True).encode(),
        )
    elif task == "business_report":
        if not draft.body.strip():
            raise ValueError("report body is required")
        body = f"# {draft.title}\n\n{draft.summary}\n\n{draft.body}\n"
        for widget in widgets:
            if not observations.get(widget.fact_ref, {}).get("metric"):
                continue

            body += "\n### " + _markdown_cell(widget.title) + "\n\n"
            for row in [widget.columns, ["---"] * len(widget.columns), *widget.rows]:
                body += "| " + " | ".join(map(_markdown_cell, row)) + " |\n"
        body = with_scope(body, observations, 2 * 1024 * 1024, requested=requested)
        file = Delivery(node_key, "report", "report.md", "text/markdown", body.encode())
    elif task == "data_analysis" and widgets:
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        for item in widgets:
            writer.writerow([_csv_cell(item.title)])
            writer.writerow([_csv_cell(v) for v in item.columns])
            writer.writerows([_csv_cell(v) for v in row] for row in item.rows)
        file = Delivery(
            node_key, "table", "analysis.csv", "text/csv", stream.getvalue().encode("utf-8-sig")
        )
    else:
        return receipt
    if len(file.data) > 512 * 1024:
        raise ValueError("delivery exceeds 512 KiB")
    receipt._delivery = file
    return receipt


def persist_delivery(tx, session, run, file, refs):
    if any(ref.get("kind") == "transient" for ref in refs):
        raise ModelError("source_not_reopenable", 409)
    step = tx.scalar(
        select(AgentStep).where(AgentStep.run_id == run.id, AgentStep.node_key == file.node_key)
    )
    if step is None or step.accepted_attempt_id is None:
        raise ModelError("artifact_attempt_missing", 409)
    identifier = uuid5(run.id, file.node_key + "/" + file.slot)
    digest = sha256(file.data).hexdigest()
    previous = tx.get(AgentArtifact, identifier)
    if previous and (previous.run_id != run.id or previous.sha256 != digest):
        raise ModelError("artifact_conflict", 409)
    if previous is None:
        used = (
            tx.scalar(
                select(func.sum(AgentArtifact.size_bytes)).where(AgentArtifact.run_id == run.id)
            )
            or 0
        )
        if used + len(file.data) > 2 * 1024 * 1024:
            raise ModelError("artifact_limit", 409)
        tx.add(
            AgentArtifact(
                id=identifier,
                run_id=run.id,
                attempt_id=step.accepted_attempt_id,
                tool_call_id=file.slot,
                name=file.name,
                media_type=file.media,
                size_bytes=len(file.data),
                sha256=digest,
                content=file.data,
                scope={
                    "owner": str(session.owner_user_id),
                    "tenant": str(session.tenant_id),
                    "workspace": str(session.workspace_id),
                    "origin": "model",
                    "source_refs": refs,
                },
                created_at=run.finished_at,
            )
        )
        tx.flush()
    return {
        "type": "view.upsert",
        "payload": {
            "objectId": str(identifier),
            "revision": 1,
            "kind": "artifact",
            "value": {
                "id": str(identifier),
                "name": file.name,
                "mediaType": file.media,
                "byteSize": len(file.data),
                "sha256": digest,
                "stepId": str(step.id),
                "toolCallId": file.slot,
            },
        },
    }
