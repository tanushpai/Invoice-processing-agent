"""Realistic synthetic data: 10 vendors, 15 POs, 25 goods receipts, 30 invoices (INR).
Scenario POs 0101-0110 line up with the sample PDFs in data/sample_invoices/."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.money import q2
from src.database import models as m
from src.database.session import get_engine, init_db
from src.matching.text import normalize_name
from src.policies.engine import DEFAULT_POLICY_NAME, DEFAULT_RULES

D = Decimal

# code, name, gstin, (account, ifsc, bank), terms, category, label-style, prefix, catalog[(sku, desc, price, gst%)]
VENDORS = [
    ("V001", "Apex Industrial Supplies Pvt Ltd", "32AABCA1001F1Z5", ("50100234561001", "HDFC0001234", "HDFC Bank"), "Net 30", "Industrial", "A", "APX",
     [("APX-BLT-M10", "Hex Bolt M10 x 60mm (box of 100)", "640.00", 18), ("APX-NUT-M10", "Hex Nut M10 (box of 100)", "410.00", 18),
      ("APX-WSH-M10", "Flat Washer M10 (box of 200)", "260.00", 18), ("APX-GLV-L", "Safety Gloves, Large (pair)", "95.00", 12)]),
    ("V002", "Malabar Packaging Co.", "32AAECM2002G1Z3", ("60200345672002", "ICIC0002345", "ICICI Bank"), "Net 45", "Packaging", "B", "MPC",
     [("MPC-BOX-L", "Corrugated Box 24x18x12 (5-ply)", "38.50", 12), ("MPC-TAPE-48", "BOPP Packing Tape 48mm x 100m", "52.00", 18),
      ("MPC-WRAP-500", "Stretch Wrap Roll 500mm", "310.00", 18)]),
    ("V003", "Kerala Office Solutions Pvt Ltd", "32AACCK3003H1Z1", ("30300456783003", "SBIN0003456", "State Bank of India"), "Net 30", "Office", "A", "KOS",
     [("KOS-A4-75", "A4 Copier Paper 75 GSM (ream)", "245.00", 12), ("KOS-TNR-85A", "Toner Cartridge 85A Compatible", "2150.00", 18),
      ("KOS-CHR-ERG", "Ergonomic Mesh Chair", "6800.00", 18)]),
    ("V004", "ABC Industrial Supplies", "32AAACA4004J1Z9", ("40400567894004", "UTIB0004567", "Axis Bank"), "Net 30", "Industrial", "C", "ABC",
     [("ABC-VLV-2", "Gate Valve 2 inch Brass", "1180.00", 18), ("ABC-PIPE-1", "GI Pipe 1 inch (6m)", "890.00", 18)]),
    ("V005", "Southern Tech Hardware Ltd", "32AADCS5005K1Z7", ("70500678905005", "KKBK0005678", "Kotak Mahindra Bank"), "Net 30", "IT hardware", "B", "STH",
     [("STH-SSD-512", "512GB SATA SSD", "3850.00", 18), ("STH-RAM-16", "16GB DDR4 SODIMM", "3200.00", 18), ("STH-KBD-USB", "USB Keyboard", "450.00", 18)]),
    ("V006", "Cochin Logistics & Services", "32AAFCC6006L1Z5", ("80600789016006", "FDRL0006789", "Federal Bank"), "Net 15", "Logistics", "C", "CLS",
     [("CLS-FRT-KL", "Freight Kochi-Bengaluru (per trip)", "18500.00", 5), ("CLS-WH-PAL", "Warehouse Storage (per pallet/month)", "450.00", 18)]),
    ("V007", "Greenleaf Stationery Mart", "32AAGCG7007M1Z3", ("90700890127007", "HDFC0007890", "HDFC Bank"), "Net 30", "Stationery", "A", "GSM",
     [("GSM-PEN-BL", "Ball Pen Blue (box of 50)", "220.00", 12), ("GSM-FLD-A4", "Box File A4", "85.00", 12), ("GSM-NTB-200", "Notebook 200 pages", "60.00", 12)]),
    ("V008", "Nilgiri Electricals Pvt Ltd", "32AAHCN8008N1Z1", ("11800901238008", "ICIC0008901", "ICICI Bank"), "Net 30", "Electrical", "B", "NEL",
     [("NEL-CBL-2.5", "Copper Cable 2.5 sq mm (90m coil)", "2450.00", 18), ("NEL-MCB-32", "MCB 32A Double Pole", "560.00", 18),
      ("NEL-LED-18", "LED Batten 18W", "340.00", 12)]),
    ("V009", "Coastal Cleaning Services", "32AAICC9009P1Z9", ("12900012349009", "SBIN0009012", "State Bank of India"), "Net 30", "Facilities", "C", "CCS",
     [("CCS-DSF-5L", "Floor Disinfectant 5L", "410.00", 18), ("CCS-MOP-HD", "Heavy Duty Mop Set", "740.00", 18),
      ("CCS-BAG-L", "Garbage Bags Large (pack of 30)", "130.00", 18)]),
    ("V010", "Zenith IT Consumables LLP", "32AAJCZ1010Q1Z7", ("13100123450010", "UTIB0010123", "Axis Bank"), "Net 45", "IT consumables", "A", "ZIT",
     [("ZIT-CAT6-305", "Cat6 Cable Box 305m", "7200.00", 18), ("ZIT-RJ45-100", "RJ45 Connectors (100pcs)", "380.00", 18)]),
]
EXTRA_ACCOUNT = {"V003": ("30300456783099", "SBIN0003456", "State Bank of India (secondary)")}

# po_number, vendor idx (1-based), days_ago, [(catalog idx, qty)], receipts [[qty per item]], tag
PO_SPECS = [
    ("PO-2026-0101", 1, 30, [(0, 50), (1, 50)], [[50, 50]], "happy"),
    ("PO-2026-0102", 2, 28, [(2, 100)], [[80]], "partial"),
    ("PO-2026-0103", 3, 60, [(0, 100)], [[40], [30], [30]], "split"),
    ("PO-2026-0104", 4, 40, [(0, 20), (1, 30)], [[20, 30]], "duplicate"),
    ("PO-2026-0105", 5, 26, [(0, 10), (1, 10)], [[10, 10]], "bank"),
    ("PO-2026-0106", 6, 24, [(0, 4)], [[4]], "ambiguous"),
    ("PO-2026-0107", 7, 22, [(0, 100), (1, 50)], [[100, 50]], "wrongtotal"),
    ("PO-2026-0108", 8, 27, [(0, 10), (1, 20)], [[10, 20]], "missingpo"),
    ("PO-2026-0109", 8, 12, [(2, 100)], [], "missingpo-other"),
    ("PO-2026-0110", 9, 23, [(0, 40), (1, 10)], [[40, 10]], "scanned"),
    ("PO-2026-0111", 1, 80, [(2, 300), (3, 150)], [[100, 50], [100, 50], [100, 50]], "bg"),
    ("PO-2026-0112", 2, 75, [(0, 1200)], [[400], [400], [400]], "bg"),
    ("PO-2026-0113", 9, 70, [(1, 60)], [[20], [20], [20]], "bg"),
    ("PO-2026-0114", 10, 65, [(0, 10), (1, 40)], [[4, 15], [3, 15], [3, 10]], "bg"),
    ("PO-2026-0115", 3, 55, [(1, 12)], [[6], [6]], "bg"),
]
# statuses for background-PO invoices (one invoice per receipt, except noted by shorter lists)
BG_INVOICE_STATUS = {
    "PO-2026-0111": ["APPROVE", "APPROVE", "APPROVE"],
    "PO-2026-0112": ["APPROVE", "HOLD"],
    "PO-2026-0113": ["APPROVE", "CORRECTION_REQUIRED", "APPROVE"],
    "PO-2026-0114": ["APPROVE", "APPROVE", "HUMAN_REVIEW"],
    "PO-2026-0115": ["APPROVE", "HOLD"],
}
# vendor idx, catalog idx, qty, status  (invoices without a PO)
MISC = [(6, 0, 1, "APPROVE"), (9, 1, 20, "APPROVE"), (10, 1, 5, "APPROVE"), (7, 0, 12, "APPROVE"), (5, 2, 3, "APPROVE"),
        (2, 1, 40, "APPROVE"), (8, 2, 30, "HUMAN_REVIEW"), (1, 3, 80, "HUMAN_REVIEW"), (3, 2, 4, "HUMAN_REVIEW"),
        (6, 0, 1, "REJECT"), (9, 1, 20, "REJECT"), (4, 0, 3, "CORRECTION_REQUIRED"), (10, 0, 2, "HOLD"), (5, 1, 6, "HOLD")]
REASONS = {
    "APPROVE": "All checks passed (seeded historical record).",
    "HOLD": "Goods receipt (3-way match): billed quantity not yet received (seeded).",
    "CORRECTION_REQUIRED": "Invoice arithmetic: total does not equal subtotal + tax (seeded).",
    "HUMAN_REVIEW": "PO reference: no purchase order on the invoice (seeded).",
    "REJECT": "Duplicate invoice check: 100% similar to an earlier invoice (seeded).",
}
ORIGINAL_DUP_DATE = date(2026, 9, 24)  # fixed so the sample duplicate PDF matches it exactly


def _vendor_row(v) -> m.Vendor:
    code, name, gstin, (acct, ifsc, bank), terms, cat, _style, _prefix, _cat = v
    row = m.Vendor(code=code, name=name, normalized_name=normalize_name(name), tax_id=gstin, payment_terms=terms, category=cat,
                   email=f"accounts@{normalize_name(name).split()[0]}.example.in")
    row.bank_accounts.append(m.VendorBankAccount(account_number=acct, ifsc=ifsc, bank_name=bank))
    if code in EXTRA_ACCOUNT:
        a, i, b = EXTRA_ACCOUNT[code]
        row.bank_accounts.append(m.VendorBankAccount(account_number=a, ifsc=i, bank_name=b))
    return row


def _items_total(lines: list[tuple[Decimal, Decimal, int]]) -> tuple[Decimal, Decimal, Decimal]:
    sub = sum((q2(q * p) for q, p, _ in lines), D(0))
    tax = sum((q2(q2(q * p) * r / 100) for q, p, r in lines), D(0))
    return q2(sub), q2(tax), q2(sub + tax)


def _add_invoice(s: Session, vendor: m.Vendor, number: str, inv_date: date, lines, status: str, po: m.PurchaseOrder | None,
                 po_items: list[m.PurchaseOrderItem] | None, prefix: str, reviewed: bool = False, dup_of: m.Invoice | None = None) -> m.Invoice:
    """lines: [(sku, desc, qty, price, rate)]"""
    nums = [(D(q), D(p), r) for _, _, q, p, r in lines]
    sub, tax, total = _items_total(nums)
    inv = m.Invoice(
        invoice_number=number, vendor_id=vendor.id, vendor_name_raw=vendor.name, vendor_tax_id=vendor.tax_id, invoice_date=inv_date,
        due_date=inv_date + timedelta(days=30), po_id=po.id if po else None, po_number_raw=po.po_number if po else None, currency="INR",
        subtotal=sub, tax=tax, total=total, payment_terms=vendor.payment_terms, bank_account=vendor.bank_accounts[0].account_number,
        source_filename=f"{prefix}_{number.replace('/', '_')}.pdf", status="APPROVE" if reviewed else status, source="seed",
        extraction_method="text", extraction_provider="seed",
        received_at=__import__("datetime").datetime.combine(inv_date, __import__("datetime").time(10, 0), tzinfo=__import__("datetime").timezone.utc),
        duplicate_of_id=dup_of.id if dup_of else None)
    for i, (sku, desc, q, p, r) in enumerate(lines, start=1):
        pi = next((x for x in (po_items or []) if x.sku == sku), None)
        line_total = q2(D(q) * D(p))
        inv.items.append(m.InvoiceItem(line_no=i, sku=sku, description=desc, quantity=D(q), unit_price=D(p), tax_rate=D(r),
                                       tax_amount=q2(line_total * r / 100), line_total=line_total, po_item_id=pi.id if pi else None))
    s.add(inv)
    s.flush()
    dec = m.Decision(invoice_id=inv.id, decision=status, engine_decision=status, policy_version=1, reasons=[REASONS[status]],
                     recommended_action="Historical record." if status != "HUMAN_REVIEW" else "Awaiting reviewer.",
                     facts={"seeded": True}, review_status="none")
    if status == "HUMAN_REVIEW":
        dec.review_status = "resolved" if reviewed else "pending"
        if reviewed:
            dec.override_decision, dec.reviewer = "APPROVE", "Priya Nair (AP Lead)"
            dec.review_note = "Verified with requester; PO was raised retrospectively. Approved."
            dec.reviewed_at = inv.received_at
    inv.decision = dec
    inv.audit_events.append(m.AuditEvent(actor="system", event_type="seeded", ts=inv.received_at,
                                         message=f"Historical invoice loaded with decision {status}."))
    if status == "HUMAN_REVIEW" and reviewed:
        inv.audit_events.append(m.AuditEvent(actor="Priya Nair (AP Lead)", event_type="human_override", ts=inv.received_at,
                                             message="Override HUMAN_REVIEW -> APPROVE: " + dec.review_note,
                                             payload={"from": "HUMAN_REVIEW", "to": "APPROVE"}))
    return inv


def seed_demo_data(s: Session, today: date | None = None) -> dict[str, int]:
    today = today or date.today()
    s.add(m.Policy(name=DEFAULT_POLICY_NAME, version=1, is_active=True, rules=dict(DEFAULT_RULES),
                   notes="Initial default policy", created_by="system"))
    vendors: list[m.Vendor] = []
    for v in VENDORS:
        row = _vendor_row(v)
        s.add(row)
        vendors.append(row)
    s.flush()

    pos: dict[str, tuple[m.PurchaseOrder, list[m.PurchaseOrderItem]]] = {}
    n_receipts = 0
    for po_no, vidx, ago, items, receipts, _tag in PO_SPECS:
        vend = vendors[vidx - 1]
        cat = VENDORS[vidx - 1][-1]
        po = m.PurchaseOrder(po_number=po_no, vendor_id=vend.id, order_date=today - timedelta(days=ago), currency="INR",
                             status="open", description=f"{VENDORS[vidx - 1][5]} materials")
        pitems = []
        for ln, (ci, qty) in enumerate(items, start=1):
            sku, desc, price, rate = cat[ci]
            pi = m.PurchaseOrderItem(line_no=ln, sku=sku, description=desc, quantity=D(qty), unit_price=D(price), tax_rate=D(rate))
            po.items.append(pi)
            pitems.append(pi)
        s.add(po)
        s.flush()
        for k, rq in enumerate(receipts, start=1):
            n_receipts += 1
            gr = m.GoodsReceipt(grn_number=f"GRN-{n_receipts:04d}", po_id=po.id, received_date=today - timedelta(days=max(ago - 3 - k * 6, 2)),
                                notes="Received at Kochi warehouse")
            for pi, q in zip(pitems, rq, strict=False):
                gr.items.append(m.GoodsReceiptItem(po_item_id=pi.id, quantity_received=D(q)))
            s.add(gr)
        pos[po_no] = (po, pitems)
    s.flush()

    n_inv = 0
    # scenario history: split invoices (40 + 30) and the original of the duplicate
    po3, pi3 = pos["PO-2026-0103"]
    v3, c3 = vendors[2], VENDORS[2][-1]
    for no, q, d_ago in (("KOS/26/0451", 40, 40), ("KOS/26/0468", 30, 24)):
        _add_invoice(s, v3, no, today - timedelta(days=d_ago), [(c3[0][0], c3[0][1], q, c3[0][2], c3[0][3])], "APPROVE", po3, pi3, "KOS")
        n_inv += 1
    po4, pi4 = pos["PO-2026-0104"]
    c4 = VENDORS[3][-1]
    _add_invoice(s, vendors[3], "ABC-INV-1024", ORIGINAL_DUP_DATE,
                        [(c4[0][0], c4[0][1], 20, c4[0][2], c4[0][3]), (c4[1][0], c4[1][1], 30, c4[1][2], c4[1][3])],
                        "APPROVE", po4, pi4, "ABC")
    n_inv += 1
    # background PO invoices (one per receipt)
    for po_no, statuses in BG_INVOICE_STATUS.items():
        po, pitems = pos[po_no]
        spec = next(p for p in PO_SPECS if p[0] == po_no)
        vend = vendors[spec[1] - 1]
        cat, prefix = VENDORS[spec[1] - 1][-1], VENDORS[spec[1] - 1][7]
        for k, st in enumerate(statuses):
            lines = []
            for (ci, _q), q in zip(spec[3], spec[4][k], strict=False):
                sku, desc, price, rate = cat[ci]
                lines.append((sku, desc, q, price, rate))
            _add_invoice(s, vend, f"{prefix}/26/{700 + n_inv}", today - timedelta(days=max(spec[2] - 8 - k * 7, 3)), lines, st, po, pitems, prefix)
            n_inv += 1
    # misc invoices without PO (some already reviewed by a human)
    first_by_vendor: dict[tuple[int, int], m.Invoice] = {}
    reviewed_seen = 0
    for i, (vidx, ci, qty, st) in enumerate(MISC):
        vend, cat, prefix = vendors[vidx - 1], VENDORS[vidx - 1][-1], VENDORS[vidx - 1][7]
        sku, desc, price, rate = cat[ci]
        dup_of = first_by_vendor.get((vidx, ci)) if st == "REJECT" else None
        number = dup_of.invoice_number if dup_of else f"{prefix}/26/{900 + i}"
        reviewed = st == "APPROVE" and i % 2 == 0
        status = "HUMAN_REVIEW" if reviewed else st
        inv = _add_invoice(s, vend, number, today - timedelta(days=5 + i * 6), [(sku, desc, qty, price, rate)], status, None, None, prefix,
                           reviewed=reviewed, dup_of=dup_of)
        if st in ("APPROVE", "REJECT") or reviewed:
            first_by_vendor.setdefault((vidx, ci), inv)
        n_inv += 1
        reviewed_seen += reviewed
    s.commit()
    return {"vendors": len(vendors), "pos": len(PO_SPECS), "receipts": n_receipts, "invoices": n_inv}


def ensure_seeded(engine=None) -> bool:
    """Create tables; seed the demo data if the vendor table is empty. Returns True if it seeded."""
    from src.database.session import get_sessionmaker
    init_db(engine)
    with get_sessionmaker(engine)() as s:
        if s.scalar(select(m.Vendor.id).limit(1)) is None:
            seed_demo_data(s)
            return True
    return False


def reset_demo_data(engine=None) -> dict[str, int]:
    from src.database.models import Base
    from src.database.session import get_sessionmaker
    eng = engine or get_engine()
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    with get_sessionmaker(eng)() as s:
        return seed_demo_data(s)
