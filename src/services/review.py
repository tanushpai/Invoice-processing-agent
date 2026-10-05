from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from src.audit.log import record
from src.database import models as m
from src.schemas.decision import Decision


def apply_override(session: Session, invoice_id: int, reviewer: str, new_decision: Decision, note: str) -> None:
    """Record a human decision. Always audited; a note and reviewer name are mandatory."""
    if not reviewer.strip():
        raise ValueError("Reviewer name is required.")
    if len(note.strip()) < 10:
        raise ValueError("Please write a note of at least 10 characters explaining the decision.")
    inv = session.get(m.Invoice, invoice_id)
    if inv is None or inv.decision is None:
        raise ValueError("Invoice not found.")
    dec = inv.decision
    before = dec.effective
    dec.override_decision, dec.reviewer, dec.review_note = new_decision.value, reviewer.strip(), note.strip()
    dec.reviewed_at, dec.review_status = datetime.now(UTC), "resolved"
    inv.status = new_decision.value
    record(session, invoice_id, reviewer.strip(), "human_override", f"{before} -> {new_decision.value}: {note.strip()}",
           {"from": before, "to": new_decision.value, "system_decision": dec.decision, "policy_version": dec.policy_version})
    session.commit()
