# """
# RAG chain: retriever (SWC Registry / FAISS) -> prompt -> LLM ->
# PydanticOutputParser -> ExplainedFinding.

# Built with modern LangChain Expression Language (LCEL) pipe syntax rather
# than the legacy `RetrievalQA` chain class (which is in maintenance mode).

# Key design decisions to prevent hallucination and inaccurate output:
#   * Temperature = 0 — deterministic, low creativity.
#   * Flagged source lines are injected into the prompt — the LLM can only
#     reason about code it can actually see.
#   * The contract's import/pragma header is injected — allows the LLM to
#     detect already-applied fixes (e.g. ReentrancyGuard already imported).
#   * SWC-pinned retrieval — the retriever prioritises the SWC doc that
#     corresponds to Slither's own mapping, rather than picking any
#     semantically-close doc.
#   * Strict grounding constraints in the prompt — the LLM is told to explain
#     only the reported finding, not to invent new ones, and to check whether
#     the fix is already present before recommending it.
# """
# from __future__ import annotations

# from langchain_core.output_parsers import PydanticOutputParser
# from langchain_core.prompts import ChatPromptTemplate
# from langchain_core.runnables import RunnableLambda, RunnablePassthrough

# from app.chains.schemas import ExplainedFinding, RawFinding
# from app.knowledge_base.vectorstore import get_retriever_for_check
# from config import settings

# # ---------------------------------------------------------------------------
# # Prompt template
# # ---------------------------------------------------------------------------

# _PROMPT = ChatPromptTemplate.from_template(
#     """\
# You are an expert smart-contract security auditor producing a structured
# explanation of a *single* static-analysis finding reported by Slither.

# ════════════════════════════════════════════════════════
# STRICT RULES — follow every rule, no exceptions:
# 1. Explain ONLY the finding described below.  Do NOT invent additional
#    findings or speculate about vulnerabilities not reported by Slither.
# 2. Base your explanation solely on the FLAGGED SOURCE LINES and SWC CONTEXT
#    provided.  Do not reason about code that is not shown.
# 3. Before writing the fix_snippet, check the CONTRACT HEADER for imports
#    and modifiers.  If the standard fix is already present (e.g.
#    `nonReentrant` modifier is used, `SafeERC20` is imported, Solidity
#    >=0.8.0 is declared), set fix_already_present=true and write
#    "Fix already applied: <reason>" in fix_snippet instead of repeating it.
# 4. Only include SWC IDs in the `references` list that appear verbatim in the
#    SWC CONTEXT below.  If the context is irrelevant to this detector, leave
#    `references` empty.
# 5. Do not copy the raw Slither description verbatim into plain_explanation;
#    rewrite it in plain English for a junior developer.
# 6. If SWC REGISTRY CONTEXT below says "(No relevant SWC Registry entry found
#    for this detector.)", this detector has NO established SWC mapping.
#    Do not borrow language, risk scenarios, or terminology from a *different*
#    vulnerability class just because it sounds related. Describe only what
#    this specific detector actually flags. In particular, do not describe a
#    "silent failure" or "unchecked return value" risk unless the flagged
#    source lines you were given actually omit a return-value check.
# ════════════════════════════════════════════════════════

# ── SWC REGISTRY CONTEXT ──────────────────────────────
# {context}
# ──────────────────────────────────────────────────────

# ── CONTRACT HEADER (first lines — imports, pragma, declarations) ──
# {contract_header}
# ──────────────────────────────────────────────────────

# ── FLAGGED SOURCE LINES ──────────────────────────────
# {source_lines}
# ──────────────────────────────────────────────────────

# ── FINDING ───────────────────────────────────────────
# Detector  : {check}
# Severity  : {severity}
# Confidence: {confidence}
# Description (raw Slither output):
# {description}
# Affected elements: {elements}
# ──────────────────────────────────────────────────────

# {format_instructions}

# Respond with ONLY the JSON object — no preamble, no markdown fences.
# """
# )


# # ---------------------------------------------------------------------------
# # LLM factory
# # ---------------------------------------------------------------------------

# def _get_llm():
#     if settings.LLM_PROVIDER == "groq":
#         from langchain_groq import ChatGroq

#         return ChatGroq(
#             model=settings.GROQ_MODEL,
#             api_key=settings.GROQ_API_KEY,
#             temperature=0,
#         )
#     elif settings.LLM_PROVIDER == "gemini":
#         from langchain_google_genai import ChatGoogleGenerativeAI

#         return ChatGoogleGenerativeAI(
#             model=settings.GEMINI_MODEL,
#             google_api_key=settings.GOOGLE_API_KEY,
#             temperature=0,
#         )
#     raise ValueError(f"Unknown LLM_PROVIDER: {settings.LLM_PROVIDER}")


# def _format_docs(docs) -> str:
#     if not docs:
#         return "(No relevant SWC Registry entry found for this detector.)"
#     return "\n\n---\n\n".join(
#         f"[{d.metadata.get('swc_id', 'unknown')}]\n{d.page_content}" for d in docs
#     )


# # ---------------------------------------------------------------------------
# # Chain builder
# # ---------------------------------------------------------------------------

# def build_explainer_chain():
#     """Builds the LCEL pipeline once so it can be reused across findings."""
#     llm = _get_llm()
#     parser = PydanticOutputParser(pydantic_object=ExplainedFinding)

#     def retrieve_context(inputs: dict) -> dict:
#         retrieve = get_retriever_for_check(
#             check=inputs["check"],
#             swc_id=inputs.get("swc_id"),
#             k=3,
#         )
#         query = f"{inputs['check']} {inputs['description']}"
#         docs = retrieve(query)
#         return {**inputs, "context": _format_docs(docs)}

#     chain = (
#         RunnableLambda(retrieve_context)
#         | RunnablePassthrough.assign(
#             format_instructions=lambda _: parser.get_format_instructions()
#         )
#         | _PROMPT
#         | llm
#         | parser
#     )
#     return chain


# # ---------------------------------------------------------------------------
# # Public API
# # ---------------------------------------------------------------------------

# def _finding_to_chain_input(finding: RawFinding) -> dict:
#     elements_str = (
#         ", ".join(e.get("name", "?") for e in finding.elements if e.get("name")) or "n/a"
#     )
#     return {
#         "check": finding.check,
#         "severity": finding.severity,
#         "confidence": finding.confidence,
#         "description": finding.description,
#         "elements": elements_str,
#         "source_lines": finding.source_lines or "(source lines unavailable)",
#         "contract_header": finding.contract_header or "(contract header unavailable)",
#         "swc_id": finding.swc_id,
#     }


# def explain_findings(findings: list[RawFinding]) -> list[ExplainedFinding]:
#     """Explain a batch of raw findings, reusing a single built chain."""
#     if not findings:
#         return []

#     chain = build_explainer_chain()
#     results: list[ExplainedFinding] = []

#     for f in findings:
#         explained: ExplainedFinding = chain.invoke(_finding_to_chain_input(f))
#         explained.finding_id = f.id

#         # --- Deterministic SWC guardrail --------------------------------
#         # SLITHER_CHECK_TO_SWC (slither_runner.py) is ground truth for
#         # which SWC ID, if any, this detector maps to. The LLM's own
#         # `references` field is prompt-enforced only, and prompt compliance
#         # is not 100% even at temperature 0 — that's what produced
#         # inconsistent output (identical "low-level-calls" findings, one
#         # tagged SWC-104, one not). Rather than hoping the LLM follows the
#         # "only cite what's grounded" instruction, we clamp its output to
#         # what we already know deterministically:
#         #   - f.swc_id set   -> references is exactly [f.swc_id]
#         #   - f.swc_id None  -> references is always empty
#         # This can only make output *more* accurate: it never adds an SWC
#         # id that Slither's own mapping didn't establish, and it never
#         # drops the one grounding reference we're confident is correct.
#         explained.references = [f.swc_id] if f.swc_id else []
#         results.append(explained)

#     return results


# def explain_finding(finding: RawFinding) -> ExplainedFinding:
#     """Convenience wrapper for explaining a single finding."""
#     return explain_findings([finding])[0]

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
6. If SWC REGISTRY CONTEXT below says "(No relevant SWC Registry entry found
   for this detector.)", this detector has NO established SWC mapping.
   Do not borrow language, risk scenarios, or terminology from a *different*
   vulnerability class just because it sounds related. Describe only what
   this specific detector actually flags. In particular, do not describe a
   "silent failure" or "unchecked return value" risk unless the flagged
   source lines you were given actually omit a return-value check.
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
# fix_snippet validation & repair.
#
# Asking an LLM to hand back Solidity code (which itself contains string
# literals like `""`) as an escaped field inside a JSON object occasionally
# drops or mismatches a delimiter during escaping — e.g. we've seen
# `msg.sender.call{value: amount}(");` come back missing a closing quote.
# A full solc parse would need the snippet wrapped in a valid contract,
# which is unreliable for partial patches; a cheap, robust net instead is
# a delimiter-balance check with one repair attempt before falling back to
# a clearly-flagged manual-review message rather than shipping broken code
# silently into the PDF/JSON report.
# ---------------------------------------------------------------------------

_PAIRS = {")": "(", "}": "{", "]": "["}
_OPENERS = set(_PAIRS.values())
_CLOSERS = set(_PAIRS.keys())


def _check_balance(snippet: str) -> tuple[bool, str]:
    """Delimiter/quote balance check, escape- and string-literal-aware."""
    stack: list[str] = []
    in_str: str | None = None
    i = 0
    while i < len(snippet):
        ch = snippet[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ("'", '"'):
            in_str = ch
            i += 1
            continue
        if ch in _OPENERS:
            stack.append(ch)
        elif ch in _CLOSERS:
            if not stack or stack[-1] != _PAIRS[ch]:
                return False, f"unmatched '{ch}' at position {i}"
            stack.pop()
        i += 1

    if in_str:
        return False, f"unterminated {in_str!r} string literal"
    if stack:
        return False, f"unclosed '{stack[-1]}'"
    return True, ""


def _repair_fix_snippet(llm, broken_snippet: str, reason: str, check: str) -> str | None:
    """One repair attempt: ask the LLM to fix only the delimiter/quoting
    issue, preserving the code's meaning. Returns the repaired snippet, or
    None if the repair itself fails validation."""
    repair_prompt = (
        "The following Solidity code snippet, generated as a security-fix "
        f"suggestion for a '{check}' finding, has a syntax problem: {reason}.\n\n"
        "Snippet:\n"
        f"{broken_snippet}\n\n"
        "Return ONLY the corrected Solidity code with the delimiter/quoting "
        "issue fixed. Do not change the logic. No markdown fences, no "
        "explanation, no preamble — just the corrected code."
    )
    try:
        response = llm.invoke(repair_prompt)
        repaired = getattr(response, "content", str(response)).strip()
        repaired = repaired.strip("`").strip()
        ok, _ = _check_balance(repaired)
        return repaired if ok else None
    except Exception:
        return None


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
    repair_llm = None  # lazily created only if a repair is actually needed
    results: list[ExplainedFinding] = []

    for f in findings:
        explained: ExplainedFinding = chain.invoke(_finding_to_chain_input(f))
        explained.finding_id = f.id

        # --- fix_snippet validation & one-shot repair --------------------
        ok, reason = _check_balance(explained.fix_snippet)
        if not ok:
            if repair_llm is None:
                repair_llm = _get_llm()
            repaired = _repair_fix_snippet(repair_llm, explained.fix_snippet, reason, f.check)
            if repaired is not None:
                explained.fix_snippet = repaired
            else:
                # Repair failed too — don't ship broken code silently.
                explained.fix_snippet = (
                    "⚠️ Automatic fix generation failed validation "
                    f"({reason}) and could not be auto-repaired. Manual "
                    "review required. Raw model output:\n" + explained.fix_snippet
                )

        # --- Deterministic SWC guardrail --------------------------------
        # SLITHER_CHECK_TO_SWC (slither_runner.py) is ground truth for
        # which SWC ID, if any, this detector maps to. The LLM's own
        # `references` field is prompt-enforced only, and prompt compliance
        # is not 100% even at temperature 0 — that's what produced
        # inconsistent output (identical "low-level-calls" findings, one
        # tagged SWC-104, one not). Rather than hoping the LLM follows the
        # "only cite what's grounded" instruction, we clamp its output to
        # what we already know deterministically:
        #   - f.swc_id set   -> references is exactly [f.swc_id]
        #   - f.swc_id None  -> references is always empty
        # This can only make output *more* accurate: it never adds an SWC
        # id that Slither's own mapping didn't establish, and it never
        # drops the one grounding reference we're confident is correct.
        explained.references = [f.swc_id] if f.swc_id else []
        results.append(explained)

    return results


def explain_finding(finding: RawFinding) -> ExplainedFinding:
    """Convenience wrapper for explaining a single finding."""
    return explain_findings([finding])[0]
