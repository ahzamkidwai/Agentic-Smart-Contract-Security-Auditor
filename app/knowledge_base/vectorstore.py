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
from langchain_community.document_loaders import (DirectoryLoader, TextLoader)
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import settings

_embeddings = None


def get_embeddings():
    global _embeddings
    if _embeddings is None:
        _embeddings = HuggingFaceEmbeddings(model_name=settings.EMBEDDING_MODEL)
    return _embeddings


def build_or_load_vectorstore() -> FAISS:
    persist_dir = Path(settings.FAISS_PERSIST_DIR)
    print("Persistent Directory:", persist_dir)
    embeddings = get_embeddings()

    print("Embeddings:", embeddings)

    index_file = persist_dir / "index.faiss"
    metadata_file = persist_dir / "index.pkl"

    already_built = (
        index_file.exists()
        and metadata_file.exists()
    )

    print("Already Built:", already_built)

    # -----------------------------------------
    # Load existing FAISS index
    # -----------------------------------------

    if already_built:
        print("Loading existing FAISS vector store...")

        vectorstore = FAISS.load_local(
            str(persist_dir),
            embeddings,
            allow_dangerous_deserialization=True,
        )

        print(
            "Loaded Vector Store:",
            vectorstore,
        )

        return vectorstore

    # -----------------------------------------
    # Build FAISS index
    # -----------------------------------------

    print("Building FAISS vector store...")

    docs = _load_swc_docs()

    print(
        "Documents:",
        len(docs),
    )

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=100,
    )

    chunks = splitter.split_documents(docs)

    print("Chunks:", len(chunks))

    if not chunks:
        raise ValueError(
            "No SWC documents were found."
        )

    # Create FAISS vector store
    vectorstore = FAISS.from_documents(
        chunks,
        embeddings,
    )

    # -----------------------------------------
    # Persist
    # -----------------------------------------

    persist_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    vectorstore.save_local(str(persist_dir))
    print("FAISS vector store saved to:", persist_dir)
    return vectorstore


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


def get_retriever(k: int = 3):
    vectorstore = build_or_load_vectorstore()
    return vectorstore.as_retriever(search_kwargs={"k": k})