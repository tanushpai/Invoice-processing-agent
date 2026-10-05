"""The only seam between business logic and any LLM. Swap providers without touching the pipeline."""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.schemas.decision import InvestigationReport
from src.schemas.invoice import RawInvoiceExtraction


class AIProviderError(Exception):
    """Provider failed (network, quota, auth)."""


class AITimeoutError(AIProviderError):
    pass


class AIInvalidResponseError(AIProviderError):
    """Model answered, but not with something that validates against our schema."""


class AIProvider(ABC):
    name: str = "base"
    supports_vision: bool = False
    is_llm: bool = True

    @abstractmethod
    def extract_invoice(self, text: str, pdf_bytes: bytes | None = None) -> RawInvoiceExtraction:
        """Semantic extraction + field-name normalisation. Must never invent values."""

    @abstractmethod
    def investigate(self, facts: dict, evidence: dict[str, str]) -> InvestigationReport:
        """Explain an exception using ONLY the supplied facts/evidence (cite evidence ids)."""
