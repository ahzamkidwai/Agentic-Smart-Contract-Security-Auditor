# audit-explainer

Turns raw Slither static-analysis findings ("`reentrancy-eth`, impact: High")
into plain-English explanations a junior developer can actually act on —
grounded in the SWC (Smart Contract Weakness Classification) Registry via a
RAG pipeline, and delivered as a structured PDF report.

> **Why this exists:** Slither/Mythril output is written for people who
> already know what "SWC-107" means. Most devs on a team don't. This tool
> sits between the raw scanner output and the person who has to fix the
> bug, adding a plain-English explanation, an impact statement, and a
> concrete fix snippet for every finding — grounded in the actual SWC
> definition, not a hallucinated one.

---

## Architecture & flow

```
Solidity repo / file
        │
        ▼
┌───────────────────────┐
│  Slither (subprocess)  │   .venv-analysis
│  --json → findings     │   (slither-analyzer, solc-select)
└───────────┬────────────┘
            │  normalize_findings()
            ▼
   list[RawFinding]  (check, severity, description, mapped SWC id)
            │
            ▼
┌────────────────────────────────────────────┐
│  RAG explainer chain (LCEL)                 │   .venv-app
│  retriever → SWC Registry docs (ChromaDB)   │   (langchain, chromadb,
│  → PromptTemplate                           │    fastapi, weasyprint)
│  → LLM (Groq llama-3.3-70b / Gemini flash)  │
│  → PydanticOutputParser                     │
└───────────┬──────────────────────────────────┘
            ▼
   list[ExplainedFinding]  (plain_explanation, why_it_matters,
                             fix_snippet, references)
            │
            ├─► SQLModel (SQLite) — persisted audit run + findings
            │
            └─► Jinja2 + WeasyPrint → PDF report
```

**Why Slither only, no Mythril:** Mythril's dependency pins on top of
solc-select/web3 fight the same conflict the two-venv split is designed to
solve, and additionally impose a Python-version ceiling that clashes with
the modern `langchain`/`chromadb` stack. Slither alone already covers the
high-signal SWC categories used here (reentrancy, unchecked calls,
unprotected withdrawals/selfdestruct, tx.origin, timestamp dependence), so
it's the right tradeoff for this project. The `SLITHER_CHECK_TO_SWC`
mapping in `app/analyzers/slither_runner.py` is the natural place to add
more detectors → SWC mappings later (or wire in a second scanner) without
touching the rest of the pipeline.

---

## Why two virtual environments

`slither-analyzer` / `solc-select` pin old, narrow ranges of `web3`,
`eth-account`, `eth-hash`, and `py-ecc`. `langchain` + `chromadb` +
`fastapi` want much newer, unrelated dependency trees. Trying to install
both into one environment causes pip's resolver to backtrack indefinitely
or fail outright — it's a genuine conflict, not a network hiccup.

Because the security tools are only ever invoked as **subprocesses** (see
`app/analyzers/slither_runner.py`), the two environments never need to
share a Python import space. So:

- **`.venv-analysis`** — `slither-analyzer`, `solc-select` only.
- **`.venv-app`** — everything else (FastAPI, LangChain, LangGraph,
  ChromaDB, SQLModel, WeasyPrint).

`config.py` resolves absolute paths to the binaries inside
`.venv-analysis` (`SLITHER_BIN`, `SOLC_SELECT_BIN`). The app process
(running in `.venv-app`) calls those binaries via `subprocess.run(...)` and
parses their `--json` output — no shared imports, no conflict.

---

## Folder structure

```
audit-explainer/
├── README.md
├── requirements.txt              # .venv-app: FastAPI, LangChain, ChromaDB, WeasyPrint...
├── requirements-analysis.txt     # .venv-analysis: slither-analyzer, solc-select only
├── .env.example
├── .gitignore
├── config.py                     # settings + two-venv binary path wiring
├── main.py                       # FastAPI entrypoint
│
├── app/
│   ├── analyzers/
│   │   └── slither_runner.py     # subprocess wrapper + finding normalization
│   ├── knowledge_base/
│   │   └── vectorstore.py        # Chroma build/load over SWC Registry docs
│   ├── chains/
│   │   ├── schemas.py            # Pydantic: RawFinding, ExplainedFinding, AuditReport
│   │   └── explainer_chain.py    # LCEL RAG chain (retriever → prompt → LLM → parser)
│   ├── reports/
│   │   ├── markdown_report.py
│   │   ├── pdf_report.py         # Jinja2 + WeasyPrint
│   │   └── templates/report_template.html
│   ├── models/
│   │   └── db_models.py          # SQLModel: AuditRun, FindingRecord
│   ├── api/
│   │   └── routes.py             # POST /audit, GET /audit/{id}, GET /audit/{id}/report.pdf
│   └── db.py                     # SQLite engine/session
│
├── data/
│   ├── swc_registry/             # SWC-101/104/105/106/107/115/116 (source docs for RAG)
│   └── chroma_db/                # generated — persisted vectorstore
│
├── scripts/
│   ├── setup_venvs.sh            # creates both venvs, installs both requirements files
│   └── ingest_knowledge_base.py  # builds the Chroma vectorstore
│
├── outputs/                      # generated PDF reports land here
│
└── tests/
    └── sample_contracts/
        └── VulnerableBank.sol    # toy contract with reentrancy / tx.origin / unprotected withdrawal
```

---

## Setup

### 1. Clone / copy this project, then create both environments

```bash
chmod +x scripts/setup_venvs.sh
./scripts/setup_venvs.sh
```

This creates `.venv-analysis` (installs `requirements-analysis.txt`,
installs solc 0.8.20 via `solc-select`) and `.venv-app` (installs
`requirements.txt`). Nothing from one file is ever installed into the
other environment.

If you'd rather do it by hand:

```bash
python3 -m venv .venv-analysis
./.venv-analysis/bin/pip install -r requirements-analysis.txt
./.venv-analysis/bin/solc-select install 0.8.20 && ./.venv-analysis/bin/solc-select use 0.8.20

python3 -m venv .venv-app
./.venv-app/bin/pip install -r requirements.txt
```

### 2. Configure your LLM key

```bash
cp .env.example .env
# edit .env and set GROQ_API_KEY (or switch LLM_PROVIDER=gemini and set GOOGLE_API_KEY)
```

Both Groq (`llama-3.3-70b-versatile`) and Gemini (`gemini-1.5-flash`) have
free tiers generous enough for this use case.

### 3. Build the knowledge base

```bash
./.venv-app/bin/python scripts/ingest_knowledge_base.py
```

This embeds `data/swc_registry/*.md` into a persisted ChromaDB collection
at `data/chroma_db/`. Re-run it any time you add more SWC docs.

### 4. Run the API

```bash
./.venv-app/bin/uvicorn main:app --reload
```

Visit `http://127.0.0.1:8000/docs` for interactive Swagger docs.

---

## Usage

**Kick off an audit** (runs in the background; returns immediately with a run id):

```bash
curl -X POST http://127.0.0.1:8000/api/audit \
  -H "Content-Type: application/json" \
  -d '{"target_path": "'"$(pwd)"'/tests/sample_contracts/VulnerableBank.sol"}'
```

```json
{"id": 1, "target": "...VulnerableBank.sol", "status": "pending", "total_findings": 0}
```

**Poll for status / structured findings:**

```bash
curl http://127.0.0.1:8000/api/audit/1
```

```json
{
  "id": 1,
  "status": "done",
  "total_findings": 3,
  "findings": [
    {
      "finding_id": "finding-0",
      "check": "reentrancy-eth",
      "severity": "High",
      "swc_id": "SWC-107",
      "plain_explanation": "This function sends ETH to the caller before it updates their recorded balance. A malicious contract can hijack that payment and call withdraw() again before the balance is reduced, draining funds far beyond what it's actually owed.",
      "why_it_matters": "This is the exact bug class behind the 2016 DAO hack; a single reentrant call can empty the whole contract.",
      "fix_snippet": "balances[msg.sender] -= amount;\n(bool ok, ) = msg.sender.call{value: amount}(\"\");\nrequire(ok, \"transfer failed\");",
      "references": ["SWC-107"]
    }
  ]
}
```

**Download the PDF report once `status` is `"done"`:**

```bash
curl -o report.pdf http://127.0.0.1:8000/api/audit/1/report.pdf
```

---

---

## Frontend (`frontend/index.html`)

A single static HTML/JS file — no build step, no framework — with two
tabs:

1. **GitHub repo** — paste a repo URL + branch, hits
   `POST /audit/project/github`, polls `GET /audit/project/{job_id}`, then
   lists every scanned file (including clean ones) with an expandable
   findings panel per file, sourced from
   `GET /audit/project/{job_id}/files/{file_id}`.
2. **Paste contract** — paste raw Solidity, hits the new
   `POST /api/audit/paste`, polls `GET /api/audit/{run_id}`, and renders
   findings + a PDF download link.

Every finding shows its **file name and exact line number** (taken from
Slither's own `source_mapping`, not the LLM), severity, plain-English
explanation, why it matters, deterministic evidence/applicability notes,
and a validated fix snippet. A collapsible "Pipeline & anti-hallucination
guardrails" panel at the top explains each stage.

**To run it:** start the API (`uvicorn main:app --reload`), then just open
`frontend/index.html` in a browser (or serve it: `python -m http.server
5500 --directory frontend`). Set the "API base" field in the top-right to
wherever uvicorn is listening (default `http://localhost:8000`) — CORS is
already enabled in `main.py` for this.

### New/changed backend surface

| Endpoint | Purpose |
|---|---|
| `POST /api/audit/paste` | New. `{code, filename}` → writes to a scratch file, runs the same pipeline as `POST /api/audit`. |
| `GET /audit/project/{job_id}/files/{file_id}` | New. Full per-finding detail (file, line, explanation, evidence) for one file. |
| `POST /audit/project/github`, `/upload`, `GET /{job_id}`, `/{job_id}/files` | Unchanged endpoints, now actually wired to the RAG explainer (previously a stub — see `_process_job`'s former TODO block) and returning real per-file, per-line findings. |

### One-time migration

The `FindingRecord`/`AuditFile` tables gained new columns (`file_name`,
`start_line`, `end_line`, `evidence`, `findings_json`, etc.). If you already
have a `data/audit_explainer.db` from a previous run, apply the migration
once:

```bash
python scripts/migrate_add_location_and_findings_json.py
```

(A fresh database created via `init_db()` already has the full current
schema — no migration needed.)

---

## Extending it

- **More SWC coverage:** drop a new `SWC-###.md` into `data/swc_registry/`,
  add the corresponding `slither check → SWC id` entry to
  `SLITHER_CHECK_TO_SWC` in `app/analyzers/slither_runner.py`, then re-run
  `scripts/ingest_knowledge_base.py`.
- **A second scanner:** since everything downstream of `normalize_findings()`
  only cares about the `RawFinding` shape, you can add another
  subprocess-based analyzer under `app/analyzers/` and merge its normalized
  findings into the same list before calling `explain_findings()`.
- **LangGraph orchestration:** the current chain is a straight-line LCEL
  pipeline; if you want retries, a "needs more context" branch, or
  multi-step reasoning per finding, `build_explainer_chain()` is the spot
  to swap in a LangGraph graph without touching the API layer.
