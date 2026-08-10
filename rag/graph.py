r"""
LangGraph state machine for explaining a single Finding.

    retrieve --> explain --> parse --(retry, up to MAX_EXPLAIN_ATTEMPTS)--> explain
                                 \--(done)--> END

Why a graph instead of a plain function call: if the LLM returns malformed
structured output (happens occasionally with any model), we want a genuine
retry loop back through the LLM call - not just a python try/except that
gives up after one shot. Modeling it as a cyclic graph also makes this the
natural place to extend later (e.g. add a "critic" node that double-checks
the explanation against retrieved context before accepting it).
"""
from typing import TypedDict, Optional

from langgraph.graph import StateGraph, END

import config
from schemas import Finding, ExplainedFinding
from rag.prompts import EXPLAIN_PROMPT, parser


class ExplainState(TypedDict):
    finding: Finding
    retrieved_context: str
    sources: list[str]
    raw_output: str
    parsed: Optional[ExplainedFinding]
    error: Optional[str]
    attempts: int


def _make_retrieve_node(vectorstore):
    def retrieve_node(state: ExplainState) -> ExplainState:
        finding = state["finding"]
        query = f"{finding.swc_id or ''} {finding.title} {finding.raw_description}"
        docs = vectorstore.similarity_search(query, k=config.RAG_TOP_K)
        state["retrieved_context"] = "\n---\n".join(d.page_content for d in docs)
        state["sources"] = sorted({d.metadata.get("swc_id", "unknown") for d in docs})
        return state
    return retrieve_node


def _make_explain_node(llm):
    def explain_node(state: ExplainState) -> ExplainState:
        finding = state["finding"]
        prompt_str = EXPLAIN_PROMPT.format(
            title=finding.title,
            swc_id=finding.swc_id or "N/A",
            severity=finding.severity.value,
            source_tool=finding.source_tool,
            contract_file=finding.contract_file,
            line_start=finding.line_start,
            line_end=finding.line_end,
            raw_description=finding.raw_description,
            retrieved_context=state["retrieved_context"],
        )
        response = llm.invoke(prompt_str)
        state["raw_output"] = response.content
        state["attempts"] = state.get("attempts", 0) + 1
        return state
    return explain_node


def _parse_node(state: ExplainState) -> ExplainState:
    try:
        parsed = parser.parse(state["raw_output"])
        finding = state["finding"]
        # trust our own tool metadata over anything the LLM echoed back
        parsed.source_tool = finding.source_tool
        parsed.swc_id = finding.swc_id
        parsed.contract_file = finding.contract_file
        parsed.contract_name = finding.contract_name
        parsed.line_start = finding.line_start
        parsed.line_end = finding.line_end
        parsed.severity = finding.severity
        parsed.sources = state["sources"]
        state["parsed"] = parsed
        state["error"] = None
    except Exception as e:
        state["parsed"] = None
        state["error"] = str(e)
    return state


def _route_after_parse(state: ExplainState) -> str:
    if state.get("parsed") is not None:
        return "done"
    if state.get("attempts", 0) < config.MAX_EXPLAIN_ATTEMPTS:
        return "retry"
    return "done"  # give up after MAX_EXPLAIN_ATTEMPTS, parsed stays None


def build_explain_graph(vectorstore, llm):
    graph = StateGraph(ExplainState)
    graph.add_node("retrieve", _make_retrieve_node(vectorstore))
    graph.add_node("explain", _make_explain_node(llm))
    graph.add_node("parse", _parse_node)

    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "explain")
    graph.add_edge("explain", "parse")
    graph.add_conditional_edges("parse", _route_after_parse, {"retry": "explain", "done": END})

    return graph.compile()


def explain_finding(finding: Finding, compiled_graph) -> Optional[ExplainedFinding]:
    initial_state: ExplainState = {
        "finding": finding,
        "retrieved_context": "",
        "sources": [],
        "raw_output": "",
        "parsed": None,
        "error": None,
        "attempts": 0,
    }
    final_state = compiled_graph.invoke(initial_state)
    return final_state.get("parsed")


def explain_all(findings: list[Finding], compiled_graph) -> list[ExplainedFinding]:
    explained = []
    for f in findings:
        result = explain_finding(f, compiled_graph)
        if result:
            explained.append(result)
    return explained
