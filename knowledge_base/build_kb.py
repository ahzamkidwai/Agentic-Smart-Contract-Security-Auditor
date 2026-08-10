"""
One-time (or re-run-when-updated) build step: clone the SWC Registry,
chunk each entry, embed with a local model, and persist to ChromaDB.

Run via: python -m scripts.build_knowledge_base
"""
import glob
import os
import subprocess
import logging

from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma
from langchain.docstore.document import Document

import config

logger = logging.getLogger(__name__)

SWC_REPO_URL = "https://github.com/SmartContractSecurity/SWC-registry.git"


def clone_swc_registry(force: bool = False) -> None:
    if config.SWC_REGISTRY_DIR.exists() and not force:
        logger.info("SWC Registry already present at %s, skipping clone", config.SWC_REGISTRY_DIR)
        return
    if config.SWC_REGISTRY_DIR.exists() and force:
        import shutil
        shutil.rmtree(config.SWC_REGISTRY_DIR)

    subprocess.run(
        ["git", "clone", "--depth", "1", SWC_REPO_URL, str(config.SWC_REGISTRY_DIR)],
        check=True,
    )


def load_swc_docs() -> list[dict]:
    entry_paths = glob.glob(str(config.SWC_REGISTRY_DIR / "entries" / "*.md"))
    docs = []
    for path in entry_paths:
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read()
        swc_id = os.path.basename(path).replace(".md", "")
        docs.append({"swc_id": swc_id, "text": text})
    return docs


def build_vectorstore() -> Chroma:
    clone_swc_registry()
    swc_docs = load_swc_docs()
    logger.info("Loaded %d SWC entries", len(swc_docs))

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.CHUNK_SIZE, chunk_overlap=config.CHUNK_OVERLAP
    )

    lc_docs = []
    for entry in swc_docs:
        for chunk in splitter.split_text(entry["text"]):
            lc_docs.append(Document(page_content=chunk, metadata={"swc_id": entry["swc_id"]}))

    embeddings = HuggingFaceEmbeddings(model_name=config.EMBEDDING_MODEL)

    vectorstore = Chroma.from_documents(
        documents=lc_docs,
        embedding=embeddings,
        persist_directory=str(config.CHROMA_DIR),
    )
    vectorstore.persist()
    logger.info("Stored %d chunks in Chroma at %s", len(lc_docs), config.CHROMA_DIR)
    return vectorstore


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    build_vectorstore()
