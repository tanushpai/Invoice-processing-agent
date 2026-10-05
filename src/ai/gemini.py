"""Gemini implementation of AIProvider. Every response is validated with Pydantic before use."""
from __future__ import annotations

import json
import re

from pydantic import ValidationError

from src.ai.prompts import EXTRACT_SYSTEM, INVESTIGATE_SYSTEM
from src.ai.provider import AIInvalidResponseError, AIProvider, AIProviderError, AITimeoutError
from src.schemas.decision import InvestigationReport
from src.schemas.invoice import RawInvoiceExtraction

MAX_TEXT_CHARS = 30_000


class GeminiProvider(AIProvider):
    name = "gemini"
    supports_vision = True

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash", timeout_s: int = 60, client=None):
        self.model = model
        self.name = f"gemini:{model}"
        if client is not None:  # injected in tests
            self._client, self._types = client, None
            return
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout_s * 1000))

    # ---- transport -------------------------------------------------------------------------------
    def _generate(self, system: str, parts: list) -> str:
        cfg = None
        if self._types is not None:
            cfg = self._types.GenerateContentConfig(
                system_instruction=system, response_mime_type="application/json", temperature=0.0)
        try:
            resp = self._client.models.generate_content(model=self.model, contents=parts, config=cfg)
        except Exception as e:  # SDK raises a variety of types; classify by message
            msg = str(e)
            if re.search(r"(?i)timed? ?out|deadline", msg):
                raise AITimeoutError(f"Gemini request timed out: {msg[:200]}") from e
            raise AIProviderError(f"Gemini request failed: {msg[:300]}") from e
        text = getattr(resp, "text", None)
        if not text:
            raise AIInvalidResponseError("Gemini returned an empty response (possibly blocked).")
        return text

    @staticmethod
    def _json(text: str) -> str:
        t = text.strip()
        t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
        return t

    def _call_validated(self, system: str, parts: list, model_cls):
        last_err = ""
        for attempt in range(2):  # one retry with the validation error fed back
            p = parts if attempt == 0 else parts + [f"Your previous answer was invalid: {last_err}. Return valid JSON only."]
            text = self._generate(system, p)
            try:
                return model_cls.model_validate(json.loads(self._json(text)))
            except (json.JSONDecodeError, ValidationError) as e:
                last_err = str(e)[:400]
        raise AIInvalidResponseError(f"Gemini returned invalid JSON for {model_cls.__name__}: {last_err}")

    # ---- AIProvider ------------------------------------------------------------------------------
    def extract_invoice(self, text: str, pdf_bytes: bytes | None = None) -> RawInvoiceExtraction:
        parts: list = []
        if pdf_bytes is not None and not text.strip() and self._types is not None:
            parts.append(self._types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"))
            parts.append("The invoice is the attached PDF (no text layer, OCR unavailable). Extract it.")
        else:
            parts.append("INVOICE TEXT (rows are reconstructed from layout; ' | ' separates table cells):\n"
                         + text[:MAX_TEXT_CHARS])
        return self._call_validated(EXTRACT_SYSTEM, parts, RawInvoiceExtraction)

    def investigate(self, facts: dict, evidence: dict[str, str]) -> InvestigationReport:
        payload = json.dumps({"facts": facts, "evidence_catalog": evidence}, default=str)[:60_000]
        return self._call_validated(INVESTIGATE_SYSTEM, [payload], InvestigationReport)
