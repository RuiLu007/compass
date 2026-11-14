"""Track current-Run Retail query observations."""

import re
from dataclasses import dataclass
from typing import Literal

from compass_agent.execution_context import uuid4
from compass_agent.schemas import (
    ChannelRunParams,
    ProductRunParams,
    RunVerifiedSummary,
)
from compass_agent.tools.business.retail_sales.handler import (
    PreparedRetailCall,
    RetailQueryResult,
    RetailRow,
)

type QueryParams = ChannelRunParams | ProductRunParams
type QueryKey = tuple[str, str, str, str, str, str]


def query_key(params: QueryParams) -> QueryKey:
    comparison = params.compare_to
    return (
        params.dimension,
        params.period.start,
        params.period.end,
        comparison.start if comparison else "",
        comparison.end if comparison else "",
        params.channel_code if isinstance(params, ProductRunParams) else "",
    )


class VerificationFailure(Exception):
    def __init__(
        self,
        level: Literal["step", "run"],
        layer: Literal["rule", "schema", "tool_result"],
        code: str,
    ):
        self.level: Literal["step", "run"] = level
        self.layer: Literal["rule", "schema", "tool_result"] = layer
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class VerifiedQuery:
    id: str
    step_id: str
    tool_call_id: str
    result: RetailQueryResult

    @property
    def ranked(self) -> list[RetailRow]:
        return sorted(
            self.result.rows,
            key=lambda row: (
                -abs(row.delta_cent) if row.delta_cent is not None else -row.paid_cent,
                row.code,
            ),
        )

    def summary(self) -> RunVerifiedSummary:
        params = self.result.params
        return RunVerifiedSummary(
            queryId=self.id,
            stepId=self.step_id,
            toolCallId=self.tool_call_id,
            dimension=self.result.dimension,
            period=params.period,
            compareTo=params.compare_to,
            channelCode=params.channel_code if isinstance(params, ProductRunParams) else None,
            rowCount=len(self.result.rows),
            paidCent=str(sum(row.paid_cent for row in self.result.rows)),
            comparePaidCent=str(sum(row.compare_paid_cent or 0 for row in self.result.rows))
            if params.compare_to
            else None,
        )


class RetailVerifier:
    def __init__(self) -> None:
        self.by_key: dict[QueryKey, VerifiedQuery] = {}
        self.by_id: dict[str, VerifiedQuery] = {}

    def observe(
        self, prepared: PreparedRetailCall, result: RetailQueryResult, step_id: str
    ) -> tuple[VerifiedQuery, bool]:
        if (
            prepared.params is None
            or result.params != prepared.params
            or result.dimension != prepared.params.dimension
        ):
            raise VerificationFailure("step", "rule", "query_scope_mismatch")
        key = query_key(prepared.params)
        if result.status == "error":
            raise VerificationFailure(
                "step", "tool_result", result.error_code or "retail_query_failed"
            )
        compared = result.params.compare_to is not None
        if (
            len(result.rows) > 200
            or (result.status == "ok") != bool(result.rows)
            or len({row.code for row in result.rows}) != len(result.rows)
            or any(not self._valid_row(row, compared) for row in result.rows)
        ):
            raise VerificationFailure("step", "schema", "invalid_result_shape")
        evidence = VerifiedQuery(str(uuid4()), step_id, prepared.tool_call_id, result)
        fresh = key not in self.by_key
        self.by_key[key] = evidence
        self.by_id[evidence.id] = evidence
        return evidence, fresh

    @staticmethod
    def _valid_row(row: RetailRow, compared: bool) -> bool:
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}", row.code)
            or type(row.paid_cent) is not int
            or not 0 <= row.paid_cent < 10**17
        ):
            return False
        if not compared:
            return row.compare_paid_cent is None and row.delta_cent is None
        return (
            type(row.compare_paid_cent) is int
            and 0 <= row.compare_paid_cent < 10**17
            and type(row.delta_cent) is int
            and row.delta_cent == row.compare_paid_cent - row.paid_cent
        )

    def partial(self) -> list[RunVerifiedSummary]:
        newest = {}
        for item in self.by_id.values():
            newest[item.result.dimension] = item
        return [item.summary() for item in newest.values()]
