"""One-off script: builds the Chroma vectorstore from data/swc_registry/*.md.

Run inside .venv-app:
    ./.venv-app/bin/python scripts/ingest_knowledge_base.py
"""
from app.knowledge_base.vectorstore import build_or_load_vectorstore

if __name__ == "__main__":
    vs = build_or_load_vectorstore()
    print(f"Vectorstore ready. Document count: {vs._collection.count()}")
