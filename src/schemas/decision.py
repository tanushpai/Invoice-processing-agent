from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Decision(StrEnum):
    APPROVE = "APPROVE"
    HOLD = "HOLD"
    CORRECTION_REQUIRED = "CORRECTION_REQUIRED"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    REJECT = "REJECT"


# Higher = more restrictive. The engine picks the most restrictive failed rule.
SEVERITY: dict[Decision, int] = {
    Decision.APPROVE: 0,
    Decision.HOLD: 1,
    Decision.CORRECTION_REQUIRED: 2,
    Decision.HUMAN_REVIEW: 3,
    Decision.REJECT: 4,
}


def most_restrictive(decisions: list[Decision]) -> Decision:
    return max(decisions, key=lambda d: SEVERITY[d], default=Decision.APPROVE)


class PolicyResult(BaseModel):
    rule_id: str
    name: str
    status: Literal["pass", "fail", "warn", "skipped"]
    impact: Decision | None = None  # decision this rule forces when it fails
    detail: str
    threshold: str = ""
    evidence: list[tuple[str, str]] = Field(default_factory=list)  # (label, value) shown to humans
    refs: list[str] = Field(default_factory=list)                  # evidence topics
    evidence_ids: list[str] = Field(default_factory=list)          # resolved E-ids


class StageEvent(BaseModel):
    key: str
    label: str
    status: Literal["ok", "warn", "skipped", "error"]
    detail: str
    duration_ms: int = 0


class Finding(BaseModel):
    title: str
    detail: str
    severity: Literal["low", "medium", "high"] = "medium"
    evidence_refs: list[str] = Field(default_factory=list)


class InvestigationReport(BaseModel):
    """What the Investigation Agent must return. Validated before it is trusted."""
    summary: str
    root_cause: str
    findings: list[Finding] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "medium"
    risk_rationale: str = ""
    recommended_action: str
    suggested_decision: Decision
    human_review_required: bool = False
    confidence: float = Field(default=0.5, ge=0, le=1)
    open_questions: list[str] = Field(default_factory=list)
