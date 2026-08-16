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

        # `swc_id is None` means SLITHER_CHECK_TO_SWC has *explicitly* recorded
        # that this detector has no reliable SWC mapping (see the table's
        # docstring). Running a generic semantic fallback here is how
        # unrelated-but-lexically-similar SWC docs (e.g. "low-level-calls"
        # pulling in SWC-104 because both mention "return value") end up in
        # the LLM's context and get hallucinated into `references`.
        # No context beats misleading context — return nothing instead.
        if swc_id is None and check in _CONFIRMED_NO_SWC_CHECKS:
            return []

        # Fall back: re-query with the detector name, but only keep hits
        # that clear a similarity bar. Below the bar we'd rather tell the
        # LLM "no relevant SWC entry" than hand it a tangential match.
        fallback_query = f"{check} {query}"
        scored = vs.similarity_search_with_relevance_scores(fallback_query, k=k)
        return [doc for doc, score in scored if score >= _MIN_RELEVANCE_SCORE]

    return _retrieve


# Checks where SLITHER_CHECK_TO_SWC pins swc_id=None on purpose (see that
# table's docstring) — for these, skip semantic fallback entirely rather
# than risk pulling in a lexically-similar but conceptually wrong SWC doc.
_CONFIRMED_NO_SWC_CHECKS = {
    "missing-zero-check",
    "events-maths",
    "events-access",
    "low-level-calls",
    "assembly",
    "dead-code",
}

# Below this cosine-similarity score, a semantic-fallback hit is treated as
# noise rather than a real match. Tune against your own corpus if you add
# more SWC docs; 0.35 was calibrated against the current 7-doc registry.
_MIN_RELEVANCE_SCORE = 0.35