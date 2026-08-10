"""
Loads the already-built Chroma store for reuse by the rag layer and API,
without re-cloning or re-embedding anything.
"""
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma

import config

_vectorstore = None


def get_vectorstore() -> Chroma:
    global _vectorstore
    if _vectorstore is None:
        is_empty = not config.CHROMA_DIR.exists() or not any(config.CHROMA_DIR.iterdir())
        if is_empty:
            raise RuntimeError(
                f"No Chroma store found at {config.CHROMA_DIR}. "
                "Run `python -m scripts.build_knowledge_base` first."
            )
        embeddings = HuggingFaceEmbeddings(model_name=config.EMBEDDING_MODEL)
        _vectorstore = Chroma(
            persist_directory=str(config.CHROMA_DIR),
            embedding_function=embeddings,
        )
    return _vectorstore
