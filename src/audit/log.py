from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from src.database import models as m


def event(actor: str, event_type: str, message: str, payload: dict | None = None) -> dict:
    """In-flight audit event (kept in workflow state, written to DB with the invoice)."""
    return {"ts": datetime.now(UTC), "actor": actor, "event_type": event_type,
            "message": message, "payload": payload}


def record(session: Session, invoice_id: int, actor: str, event_type: str, message: str,
           payload: dict | None = None) -> m.AuditEvent:
    ev = m.AuditEvent(invoice_id=invoice_id, actor=actor, event_type=event_type, message=message, payload=payload)
    session.add(ev)
    return ev
