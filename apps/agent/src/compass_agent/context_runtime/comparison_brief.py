"""Render a comparison from signed query cells, followed by explicitly attributed assessments."""

from decimal import Decimal
from typing import Literal

from pydantic import Field

from compass_agent.context_runtime.search_claims import (
    Checked,
    Proposal,
    display,
    format_checked,
    leaf,
)
from compass_agent.schemas import RUN_STREAM_LIMITS, RunContractModel


class Cell(RunContractModel):
    evidenceId: str = Field(min_length=1, max_length=36)
    row: int = Field(ge=0)
    column: str = Field(min_length=1, max_length=160)


class Comparison(RunContractModel):
    label: str = Field(min_length=1, max_length=64, description="经营范围或互斥经营单元名称")
    baseline: Cell = Field(description="基期数值，使用 permittedReferences 的可用原始列")
    current: Cell = Field(description="本期的同口径、同单位数值")


class Reference(RunContractModel):
    evidenceId: str = Field(min_length=1, max_length=36)
    row: int | None = Field(default=None, ge=0, description="查询结果需给出行号，文档引用整段")


class Note(RunContractModel):
    kind: Literal["interpretation", "action", "gap"]
    text: str = Field(
        min_length=1,
        max_length=320,
        description="用经营语言写观察、解释或建议。金额及环比由比较表计算。",
    )
    sources: list[Reference] = Field(
        min_length=1, max_length=5, description="已读原段或已查询的业务行"
    )


class ComparisonBrief(RunContractModel):
    """直接回答用户。先列总盘和有实质影响的经营单元，再结合原文和数据写解释、行动和待核事项。"""

    comparisons: list[Comparison] = Field(min_length=1, max_length=12)
    notes: list[Note] = Field(min_length=1, max_length=12)


def render_brief(run_id, draft, load):
    result = []
    if draft.comparisons:
        table = [("经营范围", "基期", "本期", "差额", "环比")]
        sources = []
        for pair in draft.comparisons:
            cells = [
                leaf(
                    Proposal(key="value", op="cell", row=c.row, column=c.column), load(c.evidenceId)
                )
                for c in (pair.baseline, pair.current)
            ]
            before, after = cells
            if (
                before.value is None
                or after.value is None
                or before.status != "supported"
                or after.status != "supported"
                or before.unit != after.unit
            ):
                raise ValueError(f"{pair.label} 需要两格同单位的有效数值")
            delta = after.value - before.value
            percent = (
                "基期为零" if not before.value else f"{delta / before.value * Decimal(100):.2f}%"
            )
            table.append(
                (
                    pair.label,
                    display(before.value, before.unit),
                    display(after.value, after.unit),
                    display(delta, before.unit),
                    percent,
                )
            )
            for cell in cells:
                sources.extend(s for s in cell.sources if s not in sources)
        result.append(
            Checked(
                "两期经营数值及变化",
                tuple(sources),
                table=tuple(table),
                calculation="差额 = 本期 − 基期，环比 = 差额 ÷ 基期",
            )
        )
    labels = {"interpretation": "经营判断", "action": "建议行动", "gap": "待验证"}
    for note in draft.notes:
        sources = []
        for ref in note.sources:
            evidence = load(ref.evidenceId)
            rec = evidence["record"]
            source = {k: evidence[k] for k in ("kind", "title", "version", "scope", "locator")}
            if evidence["kind"] in {"detail", "metric"}:
                if ref.row is None or ref.row >= len(rec["rows"]):
                    raise ValueError(f"{ref.evidenceId} 需要查询回执中的有效行号")
                row = dict(zip(rec["columns"], rec["rows"][ref.row], strict=True))
                source["excerpt"] = "，".join(
                    f"{expression} = {row[column]}"
                    for column, expression in evidence["available"].items()
                )
                source["locator"] += f" · 行 {ref.row + 1}"
            elif evidence["kind"] in {"knowledge", "history", "external", "definition"}:
                source["excerpt"] = rec["text"][:1000]
            else:
                raise ValueError("请使用实际查询结果或已经打开的原文")
            sources.append(source)
        # An interpretation or proposed action is attributed to sources. Only the
        # table above is issued as a verified arithmetic claim.
        result.append(Checked(f"{labels[note.kind]} · {note.text}", tuple(sources), "insufficient"))
    body, manifest = format_checked(run_id, result)
    if len(body.encode()) > RUN_STREAM_LIMITS["answerBytes"]:
        raise ValueError("请缩短经营简报")
    manifest["decisionComplete"] = bool(draft.comparisons) and not any(
        n.kind == "gap" for n in draft.notes
    )
    return body, manifest
