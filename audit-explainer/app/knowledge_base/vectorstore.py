"""
Chroma-backed knowledge base of SWC Registry entries.

build_or_load_vectorstore() is idempotent: if a persisted Chroma DB already
exists at settings.CHROMA_PERSIST_DIR it's loaded directly; otherwise it's
built once from the markdown files in data/swc_registry/.
"""
from __future__ import annotations

from pathlib import Path

from langchain_chroma import Chroma
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import settings

_embeddings = None


def get_embeddings():
    global _embeddings
    if _embeddings is None:
        _embeddings = HuggingFaceEmbeddings(model_name=settings.EMBEDDING_MODEL)
    return _embeddings


def build_or_load_vectorstore() -> Chroma:
    persist_dir = Path(settings.CHROMA_PERSIST_DIR)
    embeddings = get_embeddings()

    already_built = persist_dir.exists() and any(persist_dir.iterdir())

    vectorstore = Chroma(
        collection_name="swc_registry",
        embedding_function=embeddings,
        persist_directory=str(persist_dir),
    )

    if already_built and vectorstore._collection.count() > 0:
        return vectorstore

    docs = _load_swc_docs()
    splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
    chunks = splitter.split_documents(docs)
    if chunks:
        vectorstore.add_documents(chunks)
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
