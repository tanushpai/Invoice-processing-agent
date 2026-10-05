# 🧾 AP Investigator

**AI handles ambiguity and investigation. Deterministic Python handles financial correctness.**

AP Investigator takes a vendor invoice PDF and produces a reasoned, evidence-backed payment decision
(`APPROVE · HOLD · CORRECTION_REQUIRED · HUMAN_REVIEW · REJECT`). It doesn't just extract fields - it matches the
invoice against vendors, purchase orders and goods receipts, detects duplicates and split invoices, investigates
exceptions, applies versioned company policy, and knows when to ask a human.

> Case study PS-1 (Finance / AP): *invoice PDF → decision, with everything in between visible.*

## What makes it different

| Real-world AP problem | How it's handled |
|---|---|
| Every vendor labels fields differently ("Begin Date", "Period From"…) | Gemini maps them to canonical fields with per-field confidence; the label *as printed* is kept for audit |
| Ambiguous data (`03/04/2026`, a lone "Ref No") | **Never guessed** - value `null` + warning + human review. Python re-checks date ambiguity even if the LLM "resolved" it |
| LLMs make arithmetic mistakes | All money is `Decimal`; AI returns strings, Python parses and validates every number |
| Split invoices (PO 100 → 40 + 30 + 30) | Cumulative invoiced quantity across earlier invoices is tracked; the last 30 is approved, not rejected for `30 ≠ 100` |
| Partial delivery (billed 100, received 80) | Three-way match → `HOLD`, 20 units unreceived, with the exact numbers shown |
| Same invoice re-sent under another filename | Weighted duplicate score (vendor, normalised invoice no., PO, amount, date, line fingerprint, content hash) |
| Payment-diversion fraud | Bank account must match a registered vendor account; if it belongs to *another* vendor that's called out |
| "AI said approve" | A **decision guard** lets AI escalate but never relax a deterministic outcome; AI cannot reject on its own |
| Hallucinated explanations | Investigator may only cite a Python-built **evidence catalog** (E1, E2…); findings citing nothing real are dropped |
| "Why did it decide that?" | Evidence blocks, policy version, full pipeline trace and audit timeline on every invoice |

## Architecture

```
PDF → detect (text vs scanned) → PyMuPDF layout text → Tesseract OCR fallback
    → AIProvider.extract_invoice (Gemini) → Pydantic validation → Python normalisation
    → vendor match + bank check → PO match → receipts & invoice history → duplicate check
    → arithmetic + three-way reconciliation → policy engine → Investigation Agent (only if exceptions)
    → decision guard → persist evidence + audit trail
```
Orchestrated by **LangGraph** (`src/workflow/graph.py`). Each node emits one real `StageEvent`, so the live progress
panel is the actual execution trace. Any node that cannot continue safely routes to `HUMAN_REVIEW`.

```
app.py · pages/                 Streamlit UI (Dashboard, Process, History, Detail, Review, Vendors, POs, Policies)
src/ai/          AIProvider interface · GeminiProvider · OfflineProvider (no-key demo mode)
src/document/    PDF detection, text/OCR extraction, normalisation
src/matching/    vendor, bank, PO, duplicates
src/reconciliation/   invoice arithmetic, three-way match (pure functions)
src/policies/    configurable, versioned rules      src/decisions/  decision guard
src/agents/      evidence catalog, investigation agent, playbook fallback
src/workflow/    LangGraph pipeline + persistence   src/database/ models, seed data, sessions
migrations/      Alembic                             tests/         50 tests
```

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
sudo apt-get install tesseract-ocr        # or: brew install tesseract   (needed only for scanned PDFs)
cp .env.example .env                      # optional - works with no config
streamlit run app.py
```
With no `DATABASE_URL` it uses a local SQLite file and seeds demo data automatically. With no `GEMINI_API_KEY` it
runs in **offline rules mode** (a conservative reader for the sample layouts; clearly labelled in the UI).

## Deploy: Streamlit Community Cloud + Supabase

1. **Supabase** → New project → *Project Settings → Database → Connection string*. Copy the **Session pooler** URI
   (IPv4-friendly, works from Streamlit Cloud).
2. Push this folder to a GitHub repo.
3. **share.streamlit.io** → New app → pick the repo, main file `app.py`, Python 3.12.
4. App → *Settings → Secrets*, paste (see `.streamlit/secrets.toml.example`):
   ```toml
   DATABASE_URL = "postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres"
   GEMINI_API_KEY = "..."
   ```
5. First load creates tables and seeds 10 vendors / 15 POs / 25 receipts / 30 invoices. `packages.txt` installs Tesseract.

Prefer migrations over auto-create? `DATABASE_URL=... alembic upgrade head` (initial migration included).

## Demo script (≈3 minutes)

*Process Invoice → Try a sample scenario.* Run, in order: **Happy path** (APPROVE) → **Partial delivery** (HOLD, 20 units) →
**Split invoice** (APPROVE via cumulative qty) → **Duplicate** (REJECT) → **Bank mismatch** → **Ambiguous fields** →
**Wrong total** → **Missing PO** → **Scanned** (OCR path). Open *Invoice Detail* for any of them: evidence, three-way
table, investigation, policy, audit trail. Resolve a case in *Human Review*. Edit a threshold in *Policies* → new version.
Run the happy path twice to watch duplicate detection.

Sample PDFs live in `data/sample_invoices/` (regenerate with `python -m src.services.sample_pdfs`).

## Tests & lint
```bash
pytest -q        # 50 tests: schema, normalisation, money, reconciliation, split invoices, duplicates, policy,
                 # guard, all 9 scenarios end-to-end, AI failure/timeout/invalid-JSON, human override audit
ruff check .
```

## Honest limitations
- The Gemini path is covered by unit tests against an injected fake client; it has not been run against the live API in CI. Prompts are in `src/ai/prompts.py`.
- Offline mode only understands the label vocabularies of the bundled sample layouts - that's what Gemini is for.
- Vendor matching uses exact IDs + fuzzy string similarity (no embeddings); PO/vendor lookups scan in Python, fine for thousands of rows, not millions.
- Single currency (INR) reconciliation; mismatched currency is flagged for review, not converted.
- No authentication by design (take-home scope); reviewer names are self-reported.
- "Estimated time saved" on the dashboard uses an explicit assumption (8 min per invoice).
