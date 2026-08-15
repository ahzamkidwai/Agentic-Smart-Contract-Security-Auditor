"""
FAISS-backed knowledge base of SWC Registry entries.

build_or_load_vectorstore() is idempotent:
if a persisted FAISS index already exists at
settings.FAISS_PERSIST_DIR, it is loaded directly;
otherwise it is built from the markdown files in
data/swc_registry/.
"""

from __future__ import annotations

from pathlib import Path

from langchain_community.vectorstores import FAISS
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import settings


def _make_embeddings() -> HuggingFaceEmbeddings:
    return HuggingFaceEmbeddings(model_name=settings.EMBEDDING_MODEL)


# Module-level cache — reset to None to force a reload (e.g. in tests).
_vectorstore: FAISS | None = None


def build_or_load_vectorstore() -> FAISS:
    global _vectorstore
    if _vectorstore is not None:
        return _vectorstore

    persist_dir = Path(settings.FAISS_PERSIST_DIR)
    embeddings = _make_embeddings()

    index_file = persist_dir / "index.faiss"
    metadata_file = persist_dir / "index.pkl"
    already_built = index_file.exists() and metadata_file.exists()

    if already_built:
        print("Loading existing FAISS vector store...")
        _vectorstore = FAISS.load_local(
            str(persist_dir), embeddings, allow_dangerous_deserialization=True
        )
        return _vectorstore

    # -----------------------------------------
    # Build FAISS index from SWC registry docs
    # -----------------------------------------
    print("Building FAISS vector store...")
    docs = _load_swc_docs()

    if not docs:
        raise ValueError(
            "No SWC documents were found in data/swc_registry/. "
            "Add at least one .md file and re-run."
        )

    splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
    chunks = splitter.split_documents(docs)

    if not chunks:
        raise ValueError("SWC documents produced no chunks after splitting.")

    _vectorstore = FAISS.from_documents(chunks, embeddings)

    persist_dir.mkdir(parents=True, exist_ok=True)
    _vectorstore.save_local(str(persist_dir))
    print(f"FAISS vector store saved to: {persist_dir}")
    return _vectorstore


def _load_swc_docs():
    loader = DirectoryLoader(
        settings.SWC_REGISTRY_DIR,
        glob="**/*.md",
        loader_cls=TextLoader,
    )
    docs = loader.load()
    for doc in docs:
        swc_id = Path(doc.metadata["source"]).stem  # e.g. "SWC-107"
        doc.metadata["swc_id"] = swc_id
    return docs


# ---------------------------------------------------------------------------
# Retrieval helpers
# ---------------------------------------------------------------------------

def get_retriever(k: int = 3):
    """Generic semantic retriever — used as a fallback."""
    return build_or_load_vectorstore().as_retriever(search_kwargs={"k": k})


def get_retriever_for_check(
    check: str,
    swc_id: str | None,
    k: int = 3,
):
    """
    Return a retriever tuned for a specific Slither detector.

    Strategy:
    1. If `swc_id` is known, search for docs whose metadata['swc_id']
       matches that ID.  FAISS doesn't support server-side metadata
       filtering, so we fetch a larger candidate set and filter client-side,
       then fall back to semantic search if no pinned doc was found.
    2. Otherwise fall back to a pure semantic search using the detector
       name as the query.

    Returns a callable ``retrieve(query: str) -> list[Document]``.
    """
    vs = build_or_load_vectorstore()

    def _retrieve(query: str):
        candidates = vs.similarity_search(query, k=max(k * 4, 12))

        if swc_id:
            pinned = [d for d in candidates if d.metadata.get("swc_id") == swc_id]
            if pinned:
                return pinned[:k]

        # Fall back: re-query with the detector name to get a focused result
        fallback_query = f"{check} {query}"
        fallback = vs.similarity_search(fallback_query, k=k)
        return fallback

    return _retrieve