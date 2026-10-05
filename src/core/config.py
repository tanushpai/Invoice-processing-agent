"""Runtime configuration. Reads .env locally and Streamlit secrets on Streamlit Cloud."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def normalize_db_url(url: str) -> str:
    """Accept Supabase/Heroku style URLs and force the psycopg2 driver."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


@dataclass(frozen=True)
class Settings:
    database_url: str
    gemini_api_key: str | None
    gemini_model: str
    gemini_timeout_s: int
    ocr_dpi: int
    min_text_chars_per_page: int
    max_upload_mb: int

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")


def load_streamlit_secrets_into_env() -> None:
    """On Streamlit Cloud, secrets live in st.secrets; mirror them into os.environ."""
    try:
        import streamlit as st

        for key in st.secrets:
            val = st.secrets[key]
            if isinstance(val, (str, int, float)):
                os.environ.setdefault(str(key), str(val))
    except Exception:  # no secrets file locally - that's fine
        pass


def get_settings() -> Settings:
    default_db = Path(__file__).resolve().parents[2] / "data" / "ap_investigator.db"
    return Settings(
        database_url=normalize_db_url(os.getenv("DATABASE_URL") or f"sqlite:///{default_db.as_posix()}"),
        gemini_api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or None,
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        gemini_timeout_s=int(os.getenv("GEMINI_TIMEOUT_S", "60")),
        ocr_dpi=int(os.getenv("OCR_DPI", "200")),
        min_text_chars_per_page=int(os.getenv("MIN_TEXT_CHARS_PER_PAGE", "40")),
        max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "15")),
    )
