"""
RAG chain: retriever (SWC Registry / Chroma) -> prompt -> LLM ->
PydanticOutputParser -> ExplainedFinding.

Built with modern LangChain Expression Language (LCEL) pipe syntax rather
than the legacy `RetrievalQA` chain class (which is in maintenance mode).
Behaviorally it's the same "retrieve, stuff into the prompt, generate"
pattern, just composed explicitly.
"""
from __future__ import annotations

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda, RunnablePassthrough

from app.chains.schemas import ExplainedFinding, RawFinding
from app.knowledge_base.vectorstore import get_retriever
from config import settings

_PROMPT = ChatPromptTemplate.from_template(
    """You are a smart-contract security auditor explaining a static-analysis
finding to a junior developer who has no security background.

Use the SWC Registry context below ONLY to ground your explanation in the
correct vulnerability class. Do not invent SWC IDs that aren't supported
by the context.

SWC Registry context:
{context}

Finding to explain (raw Slither output):
- check: {check}
- title: {title}
- severity: {severity}
- description: {description}
- affected elements: {elements}

{format_instructions}

Respond with ONLY the JSON object, no preamble, no markdown fences.
"""
)


def _get_llm():
    if settings.LLM_PROVIDER == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=settings.GROQ_MODEL,
            api_key=settings.GROQ_API_KEY,
            temperature=0.1,
        )
    elif settings.LLM_PROVIDER == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=settings.GEMINI_MODEL,
            google_api_key=settings.GOOGLE_API_KEY,
            temperature=0.1,
        )
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.LLM_PROVIDER}")


def _format_docs(docs) -> str:
    return "\n\n---\n\n".join(
        f"[{d.metadata.get('swc_id', 'unknown')}]\n{d.page_content}" for d in docs
    )


def build_explainer_chain():
    """Builds the LCEL pipeline once so it can be reused across findings."""
    retriever = get_retriever(k=3)
    llm = _get_llm()
    parser = PydanticOutputParser(pydantic_object=ExplainedFinding)

    def retrieve_context(inputs: dict) -> dict:
        query = f"{inputs['check']} {inputs['description']}"
        docs = retriever.invoke(query)
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


def _finding_to_chain_input(finding: RawFinding) -> dict:
    elements_str = (
        ", ".join(e.get("name", "?") for e in finding.elements if e.get("name")) or "n/a"
    )
    return {
        "check": finding.check,
        "title": finding.title,
        "severity": finding.severity,
        "description": finding.description,
        "elements": elements_str,
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
        if f.swc_id and f.swc_id not in explained.references:
            explained.references.append(f.swc_id)
        results.append(explained)

    return results


def explain_finding(finding: RawFinding) -> ExplainedFinding:
    """Convenience wrapper for explaining a single finding."""
    return explain_findings([finding])[0]
