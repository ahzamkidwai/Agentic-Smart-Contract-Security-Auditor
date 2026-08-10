# Smart Contract Audit Report Explainer

Turns raw Slither/Mythril findings into a readable, grounded, per-finding
explanation report (PDF) — using LangChain for the SWC-registry RAG layer
and LangGraph for the retrieve → explain → parse (with retry) state machine.

## Project structure

```
audit-explainer/
├── config.py                # env vars, paths, model names - single source of truth
├── schemas.py                # Finding, ExplainedFinding, Severity (Pydantic)
├── analyzers/
│   ├── slither_runner.py     # Slither subprocess wrapper (project-aware)
│   ├── mythril_runner.py     # Mythril subprocess wrapper (per-file only)
│   └── normalizer.py         # unifies both tools' output, interface detection
├── knowledge_base/
│   ├── build_kb.py           # clone SWC Registry, chunk, embed, persist Chroma
│   └── retriever.py          # loads the already-built Chroma store
├── rag/
│   ├── prompts.py            # explanation prompt + Pydantic output parser
│   ├── graph.py               # LangGraph: retrieve -> explain -> parse (retry loop)
│   └── aggregator.py          # dedups Slither+Mythril findings on the same bug
├── report/
│   ├── templates/report.md.j2
│   └── generate_report.py    # Markdown -> PDF (WeasyPrint)
├── api/
│   ├── main.py                # FastAPI app
│   ├── pipeline.py            # orchestrates analyzers -> rag -> report -> db
│   └── db.py                  # SQLite persistence (SQLModel)
├── scripts/
│   ├── build_knowledge_base.py   # CLI: python -m scripts.build_knowledge_base
│   └── run_pipeline_cli.py       # CLI: python -m scripts.run_pipeline_cli <path>
├── tests/
│   ├── fixtures/               # sample contracts, incl. a pure interface
│   └── test_normalizer.py
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
└── data/                       # gitignored - chroma_store, reports, uploads, db
```

## Setup

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install -r requirements.txt

solc-select install 0.8.20
solc-select use 0.8.20

cp .env.example .env
# edit .env: set GROQ_API_KEY (console.groq.com, free tier)
```

## Build the knowledge base (run once)

```bash
python -m scripts.build_knowledge_base
```

This clones the SWC Registry into `data/swc_registry/` and builds the
persistent Chroma store at `data/chroma_store/`. Re-run with `force=True`
in `build_kb.clone_swc_registry()` if you want to refresh it later.

## Run against a single file

```bash
python -m scripts.run_pipeline_cli tests/fixtures/VulnerableBank.sol
```

## Run against a whole project folder (production case)

```bash
python -m scripts.run_pipeline_cli path/to/your/contracts_folder
```

- Slither analyzes the whole folder in one call (resolves imports/remappings
  automatically — works directly with Foundry/Hardhat project roots).
- Mythril loops per `.sol` file since it has no project mode, and skips
  pure interfaces automatically (nothing to symbolically execute in a file
  with no function bodies).

## Run the API

```bash
uvicorn api.main:app --reload
```

```bash
# analyze a single uploaded file
curl -X POST http://localhost:8000/analyze \
  -F "file=@tests/fixtures/VulnerableBank.sol"

# analyze a folder already on the server (e.g. one your CI job cloned)
curl -X POST http://localhost:8000/analyze-folder \
  -H "Content-Type: application/json" \
  -d '{"project_dir": "/path/to/contracts"}'

# fetch a past report
curl http://localhost:8000/report/1

# download the PDF
curl -OJ http://localhost:8000/report/1/pdf
```

## Run tests

```bash
pytest tests/ -v
```

These don't require Slither/Mythril to be installed — they test the
normalizer's parsing logic against mock JSON shaped like real tool output.

## LangSmith tracing (optional)

Set in `.env`:
```
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your_key
```
Every `llm.invoke()` inside the LangGraph explanation graph gets traced
automatically — no code changes needed. View traces at smith.langchain.com.

## Docker

```bash
docker compose -f docker/docker-compose.yml up --build
```

## On interfaces

Pure interfaces (no function bodies) produce no vulnerability findings from
either tool by design — there's no logic to trace. Slither still includes
them in its project scan for ERC-conformance checks; Mythril skips them
entirely via `analyzers.normalizer.is_pure_interface()` to save compute.
Abstract contracts (partial implementations) are still analyzed normally.
