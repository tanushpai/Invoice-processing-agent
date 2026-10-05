import json

import pymupdf
import pytest

from src.ai.gemini import GeminiProvider
from src.ai.offline import OfflineProvider
from src.ai.provider import AIInvalidResponseError, AIProviderError, AITimeoutError
from src.database import models as m
from src.schemas.decision import Decision, InvestigationReport
from src.services.review import apply_override
from src.services.sample_pdfs import SAMPLES as SCENARIOS


@pytest.mark.parametrize("scn", SCENARIOS, ids=[s["title"] for s in SCENARIOS])
def test_sample_scenarios_reach_expected_decision(run, scn):
    final = run(scn["file"])
    assert final["final_decision"].value == scn["expected"], [r.detail for r in final["policy_results"] if r.status == "fail"]
    assert final["invoice_id"]


def test_partial_delivery_evidence_says_20_units_missing(run):
    f = run("02_partial_delivery.pdf")
    r = next(x for x in f["policy_results"] if x.rule_id == "goods_receipt")
    assert r.status == "fail" and ("Variance", "20 units") in r.evidence and ("Goods received", "80 units") in r.evidence


def test_split_invoice_uses_history(run):
    f = run("03_split_invoice.pdf")
    ln = f["recon"].lines[0]
    assert ln.prior_invoiced_qty == 70 and ln.cumulative_invoiced_qty == 100 and f["final_decision"] == Decision.APPROVE


def test_duplicate_detected_despite_different_filename_and_number_format(run):
    f = run("04_duplicate_invoice.pdf", name="invoice.pdf")
    assert f["duplicate"].is_duplicate and f["duplicate"].top_score >= 0.9
    assert f["recon"] is None  # confirmed duplicate: three-way match deliberately skipped


def test_processing_same_file_twice_rejects_second(run):
    assert run("01_happy_path.pdf")["final_decision"] == Decision.APPROVE
    assert run("01_happy_path.pdf")["final_decision"] == Decision.REJECT


def test_bank_mismatch_flagged(run):
    f = run("05_bank_mismatch.pdf")
    assert f["bank"].status == "mismatch" and f["final_decision"] == Decision.HUMAN_REVIEW


def test_ambiguous_extraction_nulls_fields_and_goes_to_human(run):
    f = run("06_ambiguous_fields.pdf")
    inv = f["invoice"]
    assert inv.invoice_number is None and inv.service_start_date is None
    assert f["final_decision"] == Decision.HUMAN_REVIEW


def test_missing_po_suggests_candidate_but_does_not_approve(run):
    f = run("08_missing_po.pdf")
    assert f["po"].status == "inferred" and f["po"].po_number == "PO-2026-0108"
    assert f["final_decision"] == Decision.HUMAN_REVIEW


def test_scanned_pdf_uses_ocr(run):
    assert run("09_scanned_invoice.pdf")["extraction"].method == "ocr"


@pytest.mark.parametrize("data", [b"", b"not a pdf at all"])
def test_invalid_or_empty_file_routes_to_human_review(run, data):
    f = run(data)
    assert f["final_decision"] == Decision.HUMAN_REVIEW and f["fatal"]


def test_blank_pdf_routes_to_human_review(run):
    doc = pymupdf.open()
    doc.new_page()
    f = run(doc.tobytes())
    assert f["final_decision"] == Decision.HUMAN_REVIEW and "No readable content" in f["fatal"]


class FailingProvider(OfflineProvider):
    def extract_invoice(self, *a, **k):
        raise AITimeoutError("timeout")


def test_ai_failure_in_extraction_routes_to_human_review(run):
    f = run("01_happy_path.pdf", provider=FailingProvider())
    assert f["final_decision"] == Decision.HUMAN_REVIEW and "timeout" in f["fatal"]


class FlatteringProvider(OfflineProvider):
    """Tries to approve everything - the guard must not let it."""

    def investigate(self, facts, evidence):
        return InvestigationReport(summary="Looks fine!", root_cause="none", recommended_action="pay",
                                   suggested_decision=Decision.APPROVE, findings=[{"title": "ok", "detail": "fine", "evidence_refs": ["E1"]}])


def test_gemini_style_approve_is_overridden_by_guard(run):
    f = run("02_partial_delivery.pdf", provider=FlatteringProvider())
    assert f["final_decision"] == Decision.HOLD and f["guard"].notes


class UngroundedProvider(OfflineProvider):
    def investigate(self, facts, evidence):
        return InvestigationReport(summary="x", root_cause="y", recommended_action="z", suggested_decision=Decision.HOLD,
                                   findings=[{"title": "made up", "detail": "invented", "evidence_refs": ["E999"]}])


def test_ungrounded_ai_findings_are_dropped_and_fall_back(run):
    f = run("02_partial_delivery.pdf", provider=UngroundedProvider())
    assert f["investigation"].fallback_used and all(x.evidence_refs for x in f["investigation"].report.findings)


class ExplodingInvestigator(OfflineProvider):
    def investigate(self, facts, evidence):
        raise AIProviderError("quota")


def test_investigator_failure_does_not_change_deterministic_decision(run):
    f = run("02_partial_delivery.pdf", provider=ExplodingInvestigator())
    assert f["final_decision"] == Decision.HOLD and f["investigation"].fallback_used


def test_human_override_is_audited_and_validated(run, session):
    iid = run("06_ambiguous_fields.pdf")["invoice_id"]
    with pytest.raises(ValueError):
        apply_override(session, iid, "", Decision.APPROVE, "long enough note here")
    with pytest.raises(ValueError):
        apply_override(session, iid, "Asha", Decision.APPROVE, "short")
    apply_override(session, iid, "Asha", Decision.REJECT, "Vendor confirmed it was sent in error.")
    inv = session.get(m.Invoice, iid)
    assert inv.status == "REJECT" and inv.decision.decision == "HUMAN_REVIEW" and inv.decision.review_status == "resolved"
    assert any(e.event_type == "human_override" for e in inv.audit_events)


def test_every_decision_records_policy_version_and_audit_trail(run, session):
    iid = run("01_happy_path.pdf")["invoice_id"]
    inv = session.get(m.Invoice, iid)
    assert inv.decision.policy_version == 1 and len(inv.audit_events) >= 10


# ---- Gemini adapter (no network: injected fake client) -----------------------------------------------
class _Resp:
    def __init__(self, text):
        self.text = text


class _Client:
    def __init__(self, texts):
        self.texts, self.models = list(texts), self

    def generate_content(self, **kw):
        t = self.texts.pop(0)
        if isinstance(t, Exception):
            raise t
        return _Resp(t)


def test_gemini_validates_and_retries_invalid_json():
    good = json.dumps({"fields": {"total": {"value": 100, "confidence": 0.9}}, "line_items": []})
    p = GeminiProvider("k", client=_Client(["not json", f"```json\n{good}\n```"]))
    out = p.extract_invoice("text")
    assert out.fields["total"].value == "100"


def test_gemini_invalid_twice_and_timeout_map_to_typed_errors():
    with pytest.raises(AIInvalidResponseError):
        GeminiProvider("k", client=_Client(["{}x", "{}x"])).extract_invoice("t")
    with pytest.raises(AITimeoutError):
        GeminiProvider("k", client=_Client([Exception("Deadline exceeded")])).extract_invoice("t")
