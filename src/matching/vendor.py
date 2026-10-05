from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.money import mask_account
from src.database import models as m
from src.matching.text import digits, norm_id, normalize_name, similarity


class VendorCandidate(BaseModel):
    vendor_id: int
    name: str
    score: float
    methods: list[str]


class VendorMatch(BaseModel):
    status: Literal["matched", "ambiguous", "not_found"]
    vendor_id: int | None = None
    vendor_name: str | None = None
    confidence: float = 0.0
    method: str = ""
    candidates: list[VendorCandidate] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class BankCheck(BaseModel):
    status: Literal["match", "mismatch", "not_provided", "no_registered_account", "no_vendor"]
    invoice_account_masked: str | None = None
    registered_masked: list[str] = Field(default_factory=list)
    belongs_to_other_vendor: str | None = None
    note: str = ""


def match_vendor(session: Session, name: str | None, tax_id: str | None, bank_account: str | None) -> VendorMatch:
    vendors = session.scalars(select(m.Vendor).where(m.Vendor.status == "active")).all()
    inv_bank = digits(bank_account)
    cands: dict[int, VendorCandidate] = {}
    for v in vendors:
        signals: list[tuple[str, float]] = []
        if tax_id and v.tax_id and norm_id(tax_id) == norm_id(v.tax_id):
            signals.append(("tax_id", 1.0))
        if name:
            if normalize_name(name) == v.normalized_name:
                signals.append(("exact_name", 0.95))
            else:
                s = similarity(name, v.name)
                if s >= 0.75:
                    signals.append(("fuzzy_name", round(s * 0.9, 3)))
        if len(inv_bank) >= 6 and any(digits(b.account_number) == inv_bank for b in v.bank_accounts):
            signals.append(("bank_account", 0.85))
        if signals:
            best = max(s for _, s in signals)
            bonus = 0.03 * (len(signals) - 1)
            cands[v.id] = VendorCandidate(vendor_id=v.id, name=v.name, score=round(min(best + bonus, 1.0), 3),
                                          methods=[n for n, _ in signals])
    ranked = sorted(cands.values(), key=lambda c: -c.score)
    if not ranked or ranked[0].score < 0.6:
        return VendorMatch(status="not_found", candidates=ranked[:3],
                           notes=["No vendor in the approved master matches name, tax ID or bank account."])
    top = ranked[0]
    notes: list[str] = []
    if len(ranked) > 1 and ranked[0].score - ranked[1].score < 0.05:
        return VendorMatch(status="ambiguous", vendor_id=None, vendor_name=None, confidence=top.score,
                           method="+".join(top.methods), candidates=ranked[:3],
                           notes=[f"Two vendors score almost equally: {ranked[0].name} / {ranked[1].name}."])
    if top.score < 0.85:
        return VendorMatch(status="ambiguous", confidence=top.score, method="+".join(top.methods),
                           candidates=ranked[:3], notes=["Only a weak name similarity was found."])
    if "tax_id" in top.methods and "exact_name" not in top.methods and "fuzzy_name" not in top.methods and name:
        notes.append(f"Tax ID matches but printed name '{name}' differs from master '{top.name}'.")
    return VendorMatch(status="matched", vendor_id=top.vendor_id, vendor_name=top.name, confidence=top.score,
                       method="+".join(top.methods), candidates=ranked[:3], notes=notes)


def check_bank(session: Session, vendor_id: int | None, invoice_account: str | None) -> BankCheck:
    inv = digits(invoice_account)
    if vendor_id is None:
        return BankCheck(status="no_vendor", note="Vendor unknown, bank account cannot be verified.")
    if not inv:
        return BankCheck(status="not_provided", note="No bank account printed on the invoice.")
    accts = session.scalars(select(m.VendorBankAccount).where(
        m.VendorBankAccount.vendor_id == vendor_id, m.VendorBankAccount.is_active.is_(True))).all()
    reg = [digits(a.account_number) for a in accts]
    masked = [mask_account(r) for r in reg]
    if not reg:
        return BankCheck(status="no_registered_account", invoice_account_masked=mask_account(inv),
                         note="Vendor has no registered bank account to compare with.")
    if any(inv == r or (len(inv) >= 4 and len(inv) < len(r) and r.endswith(inv)) for r in reg):
        return BankCheck(status="match", invoice_account_masked=mask_account(inv), registered_masked=masked)
    other = session.scalars(select(m.VendorBankAccount).where(m.VendorBankAccount.vendor_id != vendor_id)).all()
    owner = next((a.vendor.name for a in other if digits(a.account_number) == inv), None)
    note = "Invoice bank account is not registered for this vendor."
    if owner:
        note += f" It IS registered to a different vendor: {owner}."
    return BankCheck(status="mismatch", invoice_account_masked=mask_account(inv), registered_masked=masked,
                     belongs_to_other_vendor=owner, note=note)
