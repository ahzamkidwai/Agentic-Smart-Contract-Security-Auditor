set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "== Creating .venv-analysis (slither + solc-select only) =="

py -3.11 -m venv .venv-analysis

ANALYSIS_PY="./.venv-analysis/Scripts/python.exe"
SOLC_SELECT="./.venv-analysis/Scripts/solc-select.exe"
SLITHER="./.venv-analysis/Scripts/slither.exe"

"$ANALYSIS_PY" -m pip install --upgrade pip
"$ANALYSIS_PY" -m pip install -r requirements-analysis.txt

echo "== Checking solc-select installation =="
"$SOLC_SELECT" versions

echo "== Selecting Solidity compiler version 0.8.20 =="

"$SOLC_SELECT" install 0.8.20
"$SOLC_SELECT" use 0.8.20

echo "== Verifying Solidity compiler =="

./.venv-analysis/Scripts/solc.exe --version

echo "== Verifying Slither =="

"$SLITHER" --version

echo "== Creating .venv-app (FastAPI/LangChain/ChromaDB/WeasyPrint) =="

py -3.11 -m venv .venv-app

APP_PY="./.venv-app/Scripts/python.exe"

"$APP_PY" -m pip install --upgrade pip
"$APP_PY" -m pip install -r requirements.txt

echo ""
echo "Done. Verify with:"
echo "  ./.venv-analysis/Scripts/slither.exe --version"
echo "  ./.venv-analysis/Scripts/solc.exe --version"
echo "  ./.venv-app/Scripts/python.exe -c \"import langchain, chromadb, fastapi; print('app venv OK')\""
echo ""
echo "Build the knowledge base, then run the API:"
echo "  ./.venv-app/Scripts/python.exe scripts/ingest_knowledge_base.py"
echo "  ./.venv-app/Scripts/uvicorn.exe main:app --reload"