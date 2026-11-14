"""Bounded CSV projection from an authorized product-query result."""

from __future__ import annotations

import base64
import csv
import io
from dataclasses import dataclass, field
from uuid import UUID

from compass_agent.schemas import (
    RUN_STREAM_LIMITS,
    AnalysisFile,
    AnalysisFileNone,
    AnalysisFileReady,
    AnalysisFileUnavailable,
    ProductRunParams,
)
from compass_agent.tools.business.retail_sales.handler import RetailQueryResult

CSV_FIELDS = (
    "channel_code",
    "spu_code",
    "spu_name",
    "period_start",
    "period_end",
    "compare_start",
    "compare_end",
    "paid_cent",
    "compare_paid_cent",
    "delta_cent",
)


@dataclass(frozen=True, slots=True)
class ExportCandidate:
    step_id: UUID
    tool_call_id: str
    result: RetailQueryResult = field(repr=False)


def select_export_candidate(
    current: ExportCandidate | None,
    step_id: UUID,
    tool_call_id: str,
    result: RetailQueryResult,
) -> ExportCandidate | None:
    if result.status == "ok" and result.dimension == "product" and result.rows:
        return ExportCandidate(step_id, tool_call_id, result)
    return current


def _safe_text(value: str) -> str:
    return "'" + value if value.startswith(("=", "+", "-", "@")) else value


def build_analysis_file(candidate: ExportCandidate | None, run_id: UUID) -> AnalysisFile:
    if candidate is None:
        return AnalysisFileNone(status="none")
    try:
        result = candidate.result
        if not isinstance(result.params, ProductRunParams) or not 1 <= len(result.rows) <= 200:
            return AnalysisFileUnavailable(status="unavailable", code="file_limit")
        params = result.params
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\r\n")
        writer.writerow(CSV_FIELDS)
        for row in result.rows:
            writer.writerow(
                (
                    _safe_text(params.channel_code),
                    _safe_text(row.code),
                    _safe_text(row.name),
                    params.period.start,
                    params.period.end,
                    params.compare_to.start if params.compare_to else "",
                    params.compare_to.end if params.compare_to else "",
                    row.paid_cent,
                    "" if row.compare_paid_cent is None else row.compare_paid_cent,
                    "" if row.delta_cent is None else row.delta_cent,
                )
            )
        content = output.getvalue().encode("utf-8-sig")
        if len(content) > RUN_STREAM_LIMITS["fileBytes"]:
            return AnalysisFileUnavailable(status="unavailable", code="file_limit")
        return AnalysisFileReady(
            status="ready",
            stepId=str(candidate.step_id),
            toolCallId=candidate.tool_call_id,
            filename=f"retail-products-{run_id}.csv",
            mediaType="text/csv; charset=utf-8",
            rowCount=len(result.rows),
            byteLength=len(content),
            contentBase64=base64.b64encode(content).decode("ascii"),
        )
    except Exception:
        return AnalysisFileUnavailable(status="unavailable", code="file_generation_failed")
