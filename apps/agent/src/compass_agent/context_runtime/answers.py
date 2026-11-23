"""Versioned answer candidates shared by tools, verification and publication."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class AnswerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Gap(AnswerModel):
    goal_id: str = Field(alias="goalId", min_length=1)
    reason: str = Field(min_length=1)
    next_step: str = Field(alias="nextStep", min_length=1)


class SourceRef(AnswerModel):
    evidence_id: str = Field(alias="evidenceId")
    paragraph_id: str | None = Field(default=None, alias="paragraphId")
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=1)
    row: int | None = Field(default=None, ge=0)
    column: str | None = None
    artifact_id: str | None = Field(default=None, alias="artifactId")


class ClaimRef(AnswerModel):
    claim_id: str = Field(alias="claimId")


class Calculation(AnswerModel):
    op: Literal["sum", "difference", "ratio", "percent_change"]
    inputs: list[SourceRef] = Field(min_length=1, max_length=32)
    unit: str = ""


class Claim(AnswerModel):
    id: str = Field(min_length=1)
    kind: Literal["fact", "interpretation", "recommendation"]
    text: str = Field(min_length=1)
    refs: list[SourceRef | ClaimRef] = Field(min_length=1)
    calculation: Calculation | None = None


class Block(AnswerModel):
    type: Literal["paragraph", "list", "comparison_table"]
    claim_ids: list[str] = Field(alias="claimIds", min_length=1)
    text: str | None = None


class AnswerArtifact(AnswerModel):
    format: Literal["markdown", "csv", "dashboard"]
    title: str = Field(min_length=1, max_length=160)
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds", max_length=24)


class CandidateBase(AnswerModel):
    completion: Literal["complete", "partial"]
    covered_goal_ids: list[str] = Field(alias="coveredGoalIds")
    unresolved: list[Gap]
    artifact: AnswerArtifact | None = None

    @model_validator(mode="after")
    def completion_matches_gaps(self):
        if (self.completion == "complete") == bool(self.unresolved):
            raise ValueError("完整回答的 unresolved 为空；部分回答需说明未解决目标。")
        if len(set(self.covered_goal_ids)) != len(self.covered_goal_ids):
            raise ValueError("目标 ID 重复。")
        gaps = [gap.goal_id for gap in self.unresolved]
        if len(set(gaps)) != len(gaps) or set(gaps) & set(self.covered_goal_ids):
            raise ValueError("已覆盖与未解决的目标 ID 需分别登记且保持唯一。")
        return self


class PlainAnswer(CandidateBase):
    mode: Literal["plain"]
    body: str = Field(min_length=1)


class EvidenceAnswer(CandidateBase):
    mode: Literal["evidence"]
    claims: list[Claim] = Field(min_length=1)
    blocks: list[Block] = Field(min_length=1)


ANSWER = TypeAdapter(Annotated[PlainAnswer | EvidenceAnswer, Field(discriminator="mode")])


def answer_schema():
    return {"type": "object", **ANSWER.json_schema(by_alias=True)}


class GoalReview(AnswerModel):
    goal_id: str = Field(alias="goalId")
    status: Literal["covered", "unresolved", "missing"]
    reason: str


class ClaimReview(AnswerModel):
    claim_id: str = Field(alias="claimId")
    status: Literal["supported", "unsupported", "conflicting"]
    reason: str


class AnswerReview(AnswerModel):
    candidate_digest: str = Field(alias="candidateDigest")
    evidence_revision: str = Field(alias="evidenceRevision")
    decision: Literal["accept", "act", "revise", "partial"]
    mode_valid: bool = Field(alias="modeValid")
    goals: list[GoalReview] = Field(min_length=1)
    claims: list[ClaimReview]
    feedback: str
