"""
Central configuration: env vars, paths, model names.
Every other module imports from here instead of reading os.environ directly.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

CHROMA_DIR = DATA_DIR / "chroma_store"
SWC_REGISTRY_DIR = DATA_DIR / "swc_registry"
REPORTS_DIR = DATA_DIR / "reports"
UPLOADS_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "reports.db"

for _d in (DATA_DIR, CHROMA_DIR, REPORTS_DIR, UPLOADS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- LLM / embeddings ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")

# --- Solidity ---
SOLC_VERSION = os.getenv("SOLC_VERSION", "0.8.20")

# --- LangSmith tracing (optional) ---
_tracing_enabled = os.getenv("LANGCHAIN_TRACING_V2", "false").lower() == "true"
_langsmith_key = os.getenv("LANGCHAIN_API_KEY")

if _tracing_enabled and _langsmith_key:
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = _langsmith_key
    os.environ["LANGCHAIN_PROJECT"] = os.getenv("LANGCHAIN_PROJECT", "audit-explainer")

# --- Pipeline tuning ---
RAG_TOP_K = 3
MAX_EXPLAIN_ATTEMPTS = 3
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
