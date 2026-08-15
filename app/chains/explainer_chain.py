"""
RAG chain: retriever (SWC Registry / FAISS) -> prompt -> LLM ->
PydanticOutputParser -> ExplainedFinding.

Built with modern LangChain Expression Language (LCEL) pipe syntax rather
than the legacy `RetrievalQA` chain class (which is in maintenance mode).

Key design decisions to prevent hallucination and inaccurate output:
  * Temperature = 0 — deterministic, low creativity.
  * Flagged source lines are injected into the prompt — the LLM can only
    reason about code it can actually see.
  * The contract's import/pragma header is injected — allows the LLM to
    detect already-applied fixes (e.g. ReentrancyGuard already imported).
  * SWC-pinned retrieval — the retriever prioritises the SWC doc that
    corresponds to Slither's own mapping, rather than picking any
    semantically-close doc.
  * Strict grounding constraints in the prompt — the LLM is told to explain
    only the reported finding, not to invent new ones, and to check whether
    the fix is already present before recommending it.
"""
from __future__ import annotations

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda, RunnablePassthrough

from app.chains.schemas import ExplainedFinding, RawFinding
from app.knowledge_base.vectorstore import get_retriever_for_check
from config import settings

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_PROMPT = ChatPromptTemplate.from_template(
    """\
You are an expert smart-contract security auditor producing a structured
explanation of a *single* static-analysis finding reported by Slither.

════════════════════════════════════════════════════════
STRICT RULES — follow every rule, no exceptions:
1. Explain ONLY the finding described below.  Do NOT invent additional
   findings or speculate about vulnerabilities not reported by Slither.
2. Base your explanation solely on the FLAGGED SOURCE LINES and SWC CONTEXT
   provided.  Do not reason about code that is not shown.
3. Before writing the fix_snippet, check the CONTRACT HEADER for imports
   and modifiers.  If the standard fix is already present (e.g.
   `nonReentrant` modifier is used, `SafeERC20` is imported, Solidity
   >=0.8.0 is declared), set fix_already_present=true and write
   "Fix already applied: <reason>" in fix_snippet instead of repeating it.
4. Only include SWC IDs in the `references` list that appear verbatim in the
   SWC CONTEXT below.  If the context is irrelevant to this detector, leave
   `references` empty.
5. Do not copy the raw Slither description verbatim into plain_explanation;
   rewrite it in plain English for a junior developer.
════════════════════════════════════════════════════════

── SWC REGISTRY CONTEXT ──────────────────────────────
{context}
──────────────────────────────────────────────────────

── CONTRACT HEADER (first lines — imports, pragma, declarations) ──
{contract_header}
──────────────────────────────────────────────────────

── FLAGGED SOURCE LINES ──────────────────────────────
{source_lines}
──────────────────────────────────────────────────────

── FINDING ───────────────────────────────────────────
Detector  : {check}
Severity  : {severity}
Confidence: {confidence}
Description (raw Slither output):
{description}
Affected elements: {elements}
──────────────────────────────────────────────────────

{format_instructions}

Respond with ONLY the JSON object — no preamble, no markdown fences.
"""
)


# ---------------------------------------------------------------------------
# LLM factory
# ---------------------------------------------------------------------------

def _get_llm():
    if settings.LLM_PROVIDER == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=settings.GROQ_MODEL,
            api_key=settings.GROQ_API_KEY,
            temperature=0,
        )
    elif settings.LLM_PROVIDER == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=settings.GEMINI_MODEL,
            google_api_key=settings.GOOGLE_API_KEY,
            temperature=0,
        )
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.LLM_PROVIDER}")


def _format_docs(docs) -> str:
    if not docs:
        return "(No relevant SWC Registry entry found for this detector.)"
    return "\n\n---\n\n".join(
        f"[{d.metadata.get('swc_id', 'unknown')}]\n{d.page_content}" for d in docs
    )


# ---------------------------------------------------------------------------
# Chain builder
# ---------------------------------------------------------------------------

def build_explainer_chain():
    """Builds the LCEL pipeline once so it can be reused across findings."""
    llm = _get_llm()
    parser = PydanticOutputParser(pydantic_object=ExplainedFinding)

    def retrieve_context(inputs: dict) -> dict:
        retrieve = get_retriever_for_check(
            check=inputs["check"],
            swc_id=inputs.get("swc_id"),
            k=3,
        )
        query = f"{inputs['check']} {inputs['description']}"
        docs = retrieve(query)
        return {**inputs, "context": _format_docs(docs)}

    chain = (
        RunnableLambda(retrieve_context)
        | RunnablePassthrough.assign(
            format_instructions=lambda _: parser.get_format_instructions()
        )
        | _PROMPT
        | llm
        | parser
    )
    return chain


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _finding_to_chain_input(finding: RawFinding) -> dict:
    elements_str = (
        ", ".join(e.get("name", "?") for e in finding.elements if e.get("name")) or "n/a"
    )
    return {
        "check": finding.check,
        "severity": finding.severity,
        "confidence": finding.confidence,
        "description": finding.description,
        "elements": elements_str,
        "source_lines": finding.source_lines or "(source lines unavailable)",
        "contract_header": finding.contract_header or "(contract header unavailable)",
        "swc_id": finding.swc_id,
    }


def explain_findings(findings: list[RawFinding]) -> list[ExplainedFinding]:
    """Explain a batch of raw findings, reusing a single built chain."""
    if not findings:
        return []

    chain = build_explainer_chain()
    results: list[ExplainedFinding] = []

    for f in findings:
        explained: ExplainedFinding = chain.invoke(_finding_to_chain_input(f))
        explained.finding_id = f.id
        # If Slither's own mapping resolved an SWC ID and the LLM didn't
        # include it (perhaps context was sparse), add it as a grounding
        # reference only when it wasn't already mentioned.
        if f.swc_id and f.swc_id not in explained.references:
            explained.references.append(f.swc_id)
        results.append(explained)

    return results


def explain_finding(finding: RawFinding) -> ExplainedFinding:
    """Convenience wrapper for explaining a single finding."""
    return explain_findings([finding])[0]
