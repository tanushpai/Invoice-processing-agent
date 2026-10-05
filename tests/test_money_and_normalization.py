from decimal import Decimal

from src.core.dates import parse_date
from src.core.money import fmt_inr, parse_decimal, pct_variance
from src.document.normalize import content_hash, normalize_extraction
from src.schemas.invoice import ExtractedField, RawInvoiceExtraction, RawLineItem


def test_parse_decimal_variants():
    assert parse_decimal("₹1,23,456.50") == Decimal("123456.50")
    assert parse_decimal("(120.00)") == Decimal("-120.00")
    assert parse_decimal("18%") == Decimal("18")
    assert parse_decimal("abc") is None and parse_decimal(None) is None


def test_inr_formatting_uses_indian_grouping():
    assert fmt_inr(Decimal("1234567.5")) == "₹12,34,567.50"
    assert fmt_inr(Decimal("999")) == "₹999.00"


def test_pct_variance_and_zero_guard():
    assert pct_variance(Decimal("102"), Decimal("100")) == Decimal("2.00")
    assert pct_variance(Decimal("1"), Decimal("0")) is None


def test_ambiguous_date_is_never_guessed():
    assert parse_date("03/04/2026") == (None, True)
    assert parse_date("28/04/2026")[0].isoformat() == "2026-04-28"
    assert parse_date("04-Oct-2026")[0].isoformat() == "2026-10-04"


def test_normalizer_nulls_ambiguous_date_even_if_llm_returned_iso():
    raw = RawInvoiceExtraction(fields={"service_start_date": ExtractedField(value="2026-03-04", raw_value="03/04/2026", confidence=0.9)})
    inv = normalize_extraction(raw)
    assert inv.service_start_date is None
    assert inv.meta["service_start_date"].human_review_required
    assert inv.meta["service_start_date"].confidence <= 0.42


def test_normalizer_parses_money_as_decimal_and_handles_missing_fields():
    raw = RawInvoiceExtraction(fields={"total": ExtractedField(value="61,950.00", confidence=0.9)},
                               line_items=[RawLineItem(quantity="50", unit_price="640.00", tax_rate="18%", line_total="32000")])
    inv = normalize_extraction(raw)
    assert inv.total == Decimal("61950.00") and isinstance(inv.total, Decimal)
    assert inv.line_items[0].quantity == Decimal("50")
    assert inv.invoice_number is None and "invoice_number" in inv.meta  # missing -> null, never invented


def test_content_hash_ignores_formatting():
    assert content_hash("Invoice No: ABC-1\nTotal 10") == content_hash("invoice no abc 1 total 10")
