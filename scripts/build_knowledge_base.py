"""
CLI entrypoint for Phase 2. Run once before anything else:

    python -m scripts.build_knowledge_base
"""
import logging

from knowledge_base.build_kb import build_vectorstore

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    build_vectorstore()
