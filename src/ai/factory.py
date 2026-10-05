from __future__ import annotations

from src.ai.offline import OfflineProvider
from src.ai.provider import AIProvider
from src.core.config import Settings


def get_provider(settings: Settings, prefer: str = "auto") -> AIProvider:
    """prefer: 'auto' (Gemini if key present), 'gemini', or 'offline'."""
    if prefer != "offline" and settings.gemini_api_key:
        from src.ai.gemini import GeminiProvider

        return GeminiProvider(settings.gemini_api_key, settings.gemini_model, settings.gemini_timeout_s)
    return OfflineProvider()
