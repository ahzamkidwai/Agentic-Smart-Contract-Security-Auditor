"""
Central configuration for audit-explainer.

This project deliberately runs on TWO separate virtual environments:

  .venv-analysis  -> slither-analyzer + solc-select ONLY.
                     Old, tightly-pinned deps (web3, eth-account, eth-hash, py-ecc).
                     We never import this env's packages directly - we only
                     ever shell out to its `slither` binary via subprocess.

  .venv-app       -> FastAPI, LangChain, LangGraph, ChromaDB, SQLModel,
                     WeasyPrint, etc. Modern, fast-moving stack.

Wiring: config.py exposes absolute paths to the binaries living inside
.venv-analysis (SLITHER_BIN). The app venv process just subprocess.run()s
those binaries and never tries to `pip install` them into its own
resolution space, so the two dependency trees never touch.

NOTE: Mythril is deliberately NOT used here. It still pulls dependency
ranges that fight with modern solc-select/web3 versions and additionally
imposes a python-version ceiling that clashes with the app venv's
langchain/chromadb requirements. Slither alone already covers the
majority of high-signal SWC categories (reentrancy, access control,
unchecked calls, tx.origin, timestamp dependence, etc.) needed for a
strong demo, so it's the right tradeoff for this project.
"""
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent


def _venv_bin(venv_dir: str, binary: str) -> str:
    """Resolve <venv_dir>/bin/<binary> (or Scripts\\<binary>.exe on Windows)."""
    venv_path = BASE_DIR / venv_dir
    if os.name == "nt":
        return str(venv_path / "Scripts" / f"{binary}.exe")
    return str(venv_path / "bin" / binary)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Analysis venv binaries (see module docstring) ---
    SLITHER_BIN: str = _venv_bin(".venv-analysis", "slither")
    SOLC_SELECT_BIN: str = _venv_bin(".venv-analysis", "solc-select")

    # --- LLM provider (pick ONE, both have generous free tiers) ---
    LLM_PROVIDER: str = "gemini"  # "groq" | "gemini"
    # GROQ_API_KEY: str = ""
    # GROQ_MODEL: str = "llama-3.3-70b-versatile"
    GOOGLE_API_KEY: str = "REDACTED"
    GEMINI_MODEL: str = "gemini-3.5-flash"
    HF_TOKEN: str = ""

    # --- Vector store / knowledge base ---
    CHROMA_PERSIST_DIR: str = str(BASE_DIR / "data" / "chroma_db")
    FAISS_PERSIST_DIR: str = str(BASE_DIR / "data" / "faiss_index")
    SWC_REGISTRY_DIR: str = str(BASE_DIR / "data" / "swc_registry")
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"

    # --- Persistence ---
    DATABASE_URL: str = f"sqlite:///{BASE_DIR / 'data' / 'audit_explainer.db'}"

    # --- Output ---
    REPORTS_DIR: str = str(BASE_DIR / "outputs")

    # --- Analysis run limits ---
    SLITHER_TIMEOUT_SECONDS: int = 180


settings = Settings()

# Ensure runtime dirs exist
Path(settings.REPORTS_DIR).mkdir(parents=True, exist_ok=True)
Path(settings.CHROMA_PERSIST_DIR).mkdir(parents=True, exist_ok=True)
Path(BASE_DIR / "data").mkdir(parents=True, exist_ok=True)
