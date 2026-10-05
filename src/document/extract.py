"""PDF -> text. PyMuPDF text layer first, Tesseract OCR fallback for scanned documents."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf


class DocumentError(Exception):
    pass


class InvalidPDFError(DocumentError):
    pass


class EmptyPDFError(DocumentError):
    pass


class OCRError(DocumentError):
    pass


@dataclass
class ExtractionResult:
    text: str
    method: str  # "text" | "ocr"
    page_count: int
    char_count: int
    warnings: list[str] = field(default_factory=list)
    ocr_confidence: float | None = None


def _layout_text(page: "pymupdf.Page") -> str:
    """Rebuild rows from word boxes so table cells stay on one line, separated by ' | '."""
    words = page.get_text("words")
    if not words:
        return ""
    rows: list[dict] = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        yc = (w[1] + w[3]) / 2
        if rows and abs(yc - rows[-1]["yc"]) <= 3.0:
            rows[-1]["w"].append(w)
        else:
            rows.append({"yc": yc, "w": [w]})
    lines = []
    for r in rows:
        ws = sorted(r["w"], key=lambda w: w[0])
        out = ws[0][4]
        for prev, cur in zip(ws, ws[1:], strict=False):
            out += (" | " if cur[0] - prev[2] > 18 else " ") + cur[4]
        lines.append(out)
    return "\n".join(lines)


def _alnum_len(s: str) -> int:
    return len(re.sub(r"\W", "", s))


def _ocr(doc: "pymupdf.Document", dpi: int) -> tuple[str, float | None]:
    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:  # pragma: no cover
        raise OCRError(f"OCR libraries are not installed: {e}") from e
    texts, confs = [], []
    try:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            texts.append(pytesseract.image_to_string(img, config="--psm 6"))
            data = pytesseract.image_to_data(img, config="--psm 6", output_type=pytesseract.Output.DICT)
            confs += [float(c) for c in data["conf"] if str(c).lstrip("-").isdigit() and float(c) >= 0]
    except pytesseract.TesseractNotFoundError as e:
        raise OCRError("Tesseract binary not found (install tesseract-ocr)") from e
    except Exception as e:
        raise OCRError(f"OCR failed: {e}") from e
    return "\n".join(texts), (sum(confs) / len(confs) / 100 if confs else None)


def extract_text(pdf_bytes: bytes, min_chars_per_page: int = 40, ocr_dpi: int = 200) -> ExtractionResult:
    if not pdf_bytes:
        raise EmptyPDFError("The uploaded file is empty (0 bytes).")
    if not pdf_bytes.lstrip()[:5].startswith(b"%PDF"):
        raise InvalidPDFError("The file is not a valid PDF (missing %PDF header).")
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        raise InvalidPDFError(f"The PDF could not be opened: {e}") from e
    if doc.needs_pass:
        raise InvalidPDFError("The PDF is password protected.")
    if doc.page_count == 0:
        raise EmptyPDFError("The PDF has no pages.")

    text = "\n".join(_layout_text(p) for p in doc)
    avg = _alnum_len(text) / doc.page_count
    if avg >= min_chars_per_page:
        return ExtractionResult(text=text, method="text", page_count=doc.page_count, char_count=len(text))

    ocr_text, conf = _ocr(doc, ocr_dpi)
    if _alnum_len(ocr_text) < min_chars_per_page:
        raise EmptyPDFError("No readable content found, even after OCR.")
    warns = ["Scanned document: text recovered by OCR, so digits may be misread - verify key amounts."]
    if conf is not None and conf < 0.8:
        warns.append(f"Low OCR confidence ({conf:.0%}).")
    return ExtractionResult(text=ocr_text, method="ocr", page_count=doc.page_count,
                            char_count=len(ocr_text), warnings=warns, ocr_confidence=conf)
