#!/usr/bin/env bash
# Sets up the two-venv split described in config.py.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "== Creating .venv-analysis (slither + solc-select only) =="
python3 -m venv .venv-analysis
./.venv-analysis/bin/pip install --upgrade pip
./.venv-analysis/bin/pip install -r requirements-analysis.txt

echo "== Selecting a Solidity compiler version =="
./.venv-analysis/bin/solc-select install 0.8.20
./.venv-analysis/bin/solc-select use 0.8.20

echo "== Creating .venv-app (FastAPI/LangChain/ChromaDB/WeasyPrint) =="
python3 -m venv .venv-app
./.venv-app/bin/pip install --upgrade pip
./.venv-app/bin/pip install -r requirements.txt

echo ""
echo "Done. Verify with:"
echo "  ./.venv-analysis/bin/slither --version"
echo "  ./.venv-app/bin/python -c \"import langchain, chromadb, fastapi; print('app venv OK')\""
echo ""
echo "Build the knowledge base, then run the API:"
echo "  ./.venv-app/bin/python scripts/ingest_knowledge_base.py"
echo "  ./.venv-app/bin/uvicorn main:app --reload"
