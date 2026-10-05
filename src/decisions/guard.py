"""Decision guard: the AI may ESCALATE a decision, but can never relax a deterministic outcome."""
from __future__ import annotations

from pydantic import BaseModel, Field

from src.schemas.decision import SEVERITY, Decision, InvestigationReport, PolicyResult, most_restrictive


class GuardOutcome(BaseModel):
    final: Decision
    engine_decision: Decision
    ai_suggested: Decision | None = None
    notes: list[str] = Field(default_factory=list)


def apply_guard(engine_decision: Decision, results: list[PolicyResult],
                ai: InvestigationReport | None) -> GuardOutcome:
    final, notes = engine_decision, []
    fails = [r for r in results if r.status == "fail" and r.impact]
    suggested = ai.suggested_decision if ai else None

    if suggested is not None and suggested != engine_decision:
        if SEVERITY[suggested] < SEVERITY[engine_decision]:
            why = ", ".join(r.name for r in fails) or "deterministic rules"
            notes.append(f"AI suggested {suggested.value}; overridden - policy requires {engine_decision.value} ({why}).")
        elif suggested == Decision.REJECT:
            final = max([final, Decision.HUMAN_REVIEW], key=lambda d: SEVERITY[d])
            notes.append("AI suggested REJECT; only deterministic rules may reject - escalated to HUMAN_REVIEW instead.")
        else:
            final = suggested
            notes.append(f"AI investigator escalated {engine_decision.value} -> {suggested.value}; accepted because escalation is always safe.")
    elif ai and ai.human_review_required and SEVERITY[final] < SEVERITY[Decision.HUMAN_REVIEW]:
        final = Decision.HUMAN_REVIEW
        notes.append("AI investigator requested human review; accepted (escalation).")

    # Last line of defence: APPROVE is impossible while any rule has failed.
    if final == Decision.APPROVE and fails:
        final = most_restrictive([r.impact for r in fails])
        notes.append(f"Guard blocked APPROVE: {len(fails)} policy rule(s) failed.")
    return GuardOutcome(final=final, engine_decision=engine_decision, ai_suggested=suggested, notes=notes)
