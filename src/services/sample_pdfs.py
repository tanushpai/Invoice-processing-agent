"""Generates the sample invoice PDFs (text PDFs with vendor-specific label vocab + one scanned image PDF)."""
from __future__ import annotations

import io
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pymupdf

from src.core.money import q2
from src.database.seed import VENDORS

STYLES = {
    "A": dict(vendor="Supplier", no="Invoice No", date="Invoice Date", due="Due Date", start="Start Date", end="End Date",
              po="PO Number", gstin="GSTIN", bank="Bank Account", terms="Payment Terms", sub="Subtotal", tax="Total GST",
              total="Grand Total", cols=["Description", "SKU", "Qty", "Unit Price", "GST %", "Amount"]),
    "B": dict(vendor="Seller", no="Bill No", date="Bill Date", due="Pay By", start="Begin Date", end="Completion Date",
              po="Purchase Order", gstin="GST No", bank="Account Number", terms="Terms", sub="Taxable Value", tax="GST Amount",
              total="Total Payable", cols=["Item", "Material Code", "Quantity", "Rate", "Tax %", "Net Amount"]),
    "C": dict(vendor="Billed From", no="Inv #", date="Dated", due="Payment Due", start="Period From", end="Period To",
              po="Your Order No", gstin="GSTIN", bank="A/C No", terms="Payment Terms", sub="Sub Total", tax="Total Tax",
              total="Amount Due", cols=["Particulars", "Code", "Qty", "Price", "GST %", "Value"]),
}

TODAY = date(2026, 10, 4)


def _fmt(d: date) -> str:
    return d.strftime("%d-%b-%Y")


def _money(x: Decimal) -> str:
    return f"{q2(x):,.2f}"


# file, title, expected decision, what it demonstrates, vendor idx, po, invoice no, [(catalog idx, qty)], options
SAMPLES = [
    dict(file="01_happy_path.pdf", title="Happy path", expected="APPROVE", vendor=1, po="PO-2026-0101", no="APX/26/0612",
         lines=[(0, 50), (1, 50)], note="Clean invoice; PO, goods receipt, vendor, bank and arithmetic all agree."),
    dict(file="02_partial_delivery.pdf", title="Partial delivery", expected="HOLD", vendor=2, po="PO-2026-0102", no="MPC/26/0338",
         lines=[(2, 100)], note="Invoice bills 100 units, but only 80 were received - 20 units outstanding."),
    dict(file="03_split_invoice.pdf", title="Split invoice (cumulative)", expected="APPROVE", vendor=3, po="PO-2026-0103", no="KOS/26/0502",
         lines=[(0, 30)], note="PO = 100; earlier invoices billed 40 + 30; this one bills the last 30. Must NOT be rejected for 30 != 100."),
    dict(file="04_duplicate_invoice.pdf", title="Duplicate (different filename/format)", expected="REJECT", vendor=4, po="PO-2026-0104",
         no="ABC/INV/1024", lines=[(0, 20), (1, 30)], date=date(2026, 9, 24), alt_name="invoice.pdf",
         note="Same invoice as ABC-INV-1024 already in the system, re-sent with different number punctuation and layout."),
    dict(file="05_bank_mismatch.pdf", title="Bank account mismatch", expected="HUMAN_REVIEW", vendor=5, po="PO-2026-0105", no="STH/26/0221",
         lines=[(0, 10), (1, 10)], bank="50100999888777", note="Everything matches except the bank account - classic payment-diversion fraud pattern."),
    dict(file="06_ambiguous_fields.pdf", title="Ambiguous fields", expected="HUMAN_REVIEW", vendor=6, po="PO-2026-0106", no=None,
         lines=[(0, 4)], ambiguous=True, note="Only a lone 'Ref No' (invoice or PO?) and a 03/04/2026 date (DD/MM or MM/DD?). The system refuses to guess."),
    dict(file="07_wrong_total.pdf", title="Incorrect invoice total", expected="CORRECTION_REQUIRED", vendor=7, po="PO-2026-0107", no="GSM/26/0145",
         lines=[(0, 100), (1, 50)], total_bump=Decimal("1000"), note="Subtotal + GST does not equal the printed total (off by Rs 1,000)."),
    dict(file="08_missing_po.pdf", title="Missing PO reference", expected="HUMAN_REVIEW", vendor=8, po=None, no="NEL/26/0309",
         lines=[(0, 10), (1, 20)], note="No PO on the invoice. System infers the likely PO from vendor + lines but will not auto-approve."),
    dict(file="09_scanned_invoice.pdf", title="Scanned image PDF (OCR path)", expected="APPROVE", vendor=9, po="PO-2026-0110", no="CCS/26/0077",
         lines=[(0, 40), (1, 10)], scanned=True, note="Image-only PDF with no text layer - exercises the Tesseract OCR fallback."),
]


def build_spec(s: dict) -> dict:
    v = VENDORS[s["vendor"] - 1]
    cat = v[-1]
    lines = []
    for ci, qty in s["lines"]:
        sku, desc, price, rate = cat[ci]
        lt = q2(Decimal(qty) * Decimal(price))
        lines.append(dict(desc=desc, sku=sku, qty=qty, price=Decimal(price), rate=rate, total=lt, tax=q2(lt * rate / 100)))
    sub = sum((x["total"] for x in lines), Decimal(0))
    tax = sum((x["tax"] for x in lines), Decimal(0))
    inv_date = s.get("date", TODAY)
    return dict(vendor=v[1], gstin=v[2], bank=s.get("bank", v[3][0]), terms=v[4], style=STYLES[v[6]], po=s.get("po"), no=s["no"],
                date=inv_date, due=inv_date + timedelta(days=30), lines=lines, sub=sub, tax=tax,
                total=sub + tax + s.get("total_bump", Decimal(0)), ambiguous=s.get("ambiguous", False),
                service=v[5] in ("Logistics", "Facilities"), city="Kochi, Kerala 682030")


def render_pdf(spec: dict) -> bytes:
    st = spec["style"]
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    y = 62

    def text(x, yy, t, size=10, font="helv"):
        page.insert_text((x, yy), t, fontsize=size, fontname=font)

    def rtext(xr, yy, t, size=9, font="helv"):
        page.insert_text((xr - pymupdf.get_text_length(t, fontname=font, fontsize=size), yy), t, fontsize=size, fontname=font)

    text(40, y, spec["vendor"].upper(), 16, "hebo")
    y += 16
    text(40, y, spec["city"] + "  |  accounts@" + spec["vendor"].split()[0].lower() + ".example.in", 8.5)
    y += 22
    text(40, y, "TAX INVOICE", 13, "hebo")
    y += 8
    page.draw_line((40, y), (555, y), width=0.8)
    y += 20
    rows = [(st["vendor"], spec["vendor"])]
    if spec["no"]:
        rows.append((st["no"], spec["no"]))
    else:
        rows.append(("Ref No", "4471"))
    if spec["ambiguous"]:
        rows += [(st["date"], _fmt(spec["date"])), (st["start"], "03/04/2026"), (st["end"], "28/04/2026")]
    else:
        rows += [(st["date"], _fmt(spec["date"])), (st["due"], _fmt(spec["due"]))]
        if spec["service"]:
            rows += [(st["start"], _fmt(spec["date"] - timedelta(days=28))), (st["end"], _fmt(spec["date"] - timedelta(days=1)))]
    if spec["po"]:
        rows.append((st["po"], spec["po"]))
    rows += [(st["gstin"], spec["gstin"]), (st["bank"], spec["bank"]), (st["terms"], spec["terms"]), ("Currency", "INR")]
    for k, v in rows:
        text(40, y, f"{k}: {v}", 10)
        y += 15
    y += 10
    cols = st["cols"]
    page.draw_rect(pymupdf.Rect(40, y - 11, 555, y + 5), fill=(0.92, 0.94, 0.97), color=None)
    text(44, y, cols[0], 9, "hebo"); text(235, y, cols[1], 9, "hebo")
    for xr, c in zip((365, 440, 495, 553), cols[2:], strict=False):
        rtext(xr, y, c, 9, "hebo")
    y += 18
    for ln in spec["lines"]:
        text(44, y, ln["desc"], 9)
        text(235, y, ln["sku"], 9)
        for xr, val in zip((365, 440, 495, 553), (str(ln["qty"]), _money(ln["price"]), f"{ln['rate']}%", _money(ln["total"])), strict=False):
            rtext(xr, y, val, 9)
        y += 16
    page.draw_line((40, y - 6), (555, y - 6), width=0.5)
    y += 14
    text(40, y, f"{st['sub']}: {_money(spec['sub'])}", 10); y += 15
    text(40, y, f"{st['tax']}: {_money(spec['tax'])}", 10); y += 15
    text(40, y, f"{st['total']}: {_money(spec['total'])}", 11, "hebo"); y += 40
    text(40, y, "Goods once sold will not be taken back. Subject to Kochi jurisdiction.", 8)
    text(380, y + 30, "Authorised Signatory", 9)
    return doc.tobytes()


def make_scanned(pdf_bytes: bytes) -> bytes:
    """Rasterise the page, add skew/noise/JPEG compression, and wrap as an image-only PDF (no text layer)."""
    import random

    from PIL import Image, ImageFilter

    src = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    pix = src[0].get_pixmap(dpi=170)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
    img = img.rotate(0.35, resample=Image.BICUBIC, fillcolor=255).filter(ImageFilter.GaussianBlur(0.5))
    rnd = random.Random(7)
    px = img.load()
    for _ in range(int(pix.width * pix.height * 0.004)):
        px[rnd.randrange(pix.width), rnd.randrange(pix.height)] = rnd.randrange(120, 255)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=62)
    out = pymupdf.open()
    pg = out.new_page(width=595, height=842)
    pg.insert_image(pg.rect, stream=buf.getvalue())
    return out.tobytes()


def generate_all(out_dir: Path) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for s in SAMPLES:
        pdf = render_pdf(build_spec(s))
        if s.get("scanned"):
            pdf = make_scanned(pdf)
        (out_dir / s["file"]).write_bytes(pdf)
        manifest.append({k: s[k] for k in ("file", "title", "expected", "note")} | {"upload_name": s.get("alt_name", s["file"])})
    return manifest


def sample_manifest() -> list[dict]:
    return [{k: s[k] for k in ("file", "title", "expected", "note")} | {"upload_name": s.get("alt_name", s["file"])} for s in SAMPLES]


if __name__ == "__main__":
    generate_all(Path(__file__).resolve().parents[2] / "data" / "sample_invoices")
    print("generated")
