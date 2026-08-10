"""
Orchestrates the full pipeline: static analysis -> LangGraph explanation
-> aggregation -> report generation -> persistence.

This is the single place both the FastAPI layer and the CLI script call
into, so they can never drift out of sync with each other.
"""
import uuid
from functools import lru_cache

from langchain_groq import ChatGroq

import config
from schemas import ExplainedFinding
from analyzers.normalizer import analyze_single_file, analyze_project
from knowledge_base.retriever import get_vectorstore
from rag.graph import build_explain_graph, explain_all
from rag.aggregator import aggregate_findings
from report.generate_report import generate_report
from api.db import save_report_record


@lru_cache(maxsize=1)
def get_llm() -> ChatGroq:
    if not config.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not set - copy .env.example to .env and fill it in.")
    return ChatGroq(model=config.GROQ_MODEL, temperature=0, api_key=config.GROQ_API_KEY)


@lru_cache(maxsize=1)
def get_compiled_graph():
    return build_explain_graph(get_vectorstore(), get_llm())


def _finalize(raw_findings, contract_source: str) -> tuple[list[ExplainedFinding], str, int]:
    graph = get_compiled_graph()
    explained = explain_all(raw_findings, graph)
    final_findings = aggregate_findings(explained)

    pdf_path = str(config.REPORTS_DIR / f"{uuid.uuid4().hex}.pdf")
    generate_report(final_findings, [contract_source], pdf_path)

    report_id = save_report_record(contract_source, final_findings, pdf_path)
    return final_findings, pdf_path, report_id


def analyze_contract_file(contract_path: str, original_filename: str):
    """Single-file analysis - e.g. one uploaded .sol file."""
    raw_findings = analyze_single_file(contract_path, original_filename)
    return _finalize(raw_findings, original_filename)


def analyze_contract_folder(project_dir: str):
    """Whole-project analysis - Slither runs once at project root, Mythril loops per file."""
    raw_findings = analyze_project(project_dir)
    return _finalize(raw_findings, project_dir)
