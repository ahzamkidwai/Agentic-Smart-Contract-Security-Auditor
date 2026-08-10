"""
Prompt + structured output parser for the explanation step.
"""
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate

from schemas import ExplainedFinding

parser = PydanticOutputParser(pydantic_object=ExplainedFinding)

EXPLAIN_PROMPT = PromptTemplate(
    template="""You are a smart contract security expert explaining a static analysis
finding to a developer who is not a security specialist.

Finding: {title} ({swc_id})
Severity: {severity}
Detected by: {source_tool}
Location: {contract_file}, lines {line_start}-{line_end}
Raw tool output: {raw_description}

Relevant reference material retrieved from the SWC Registry:
{retrieved_context}

Explain this finding for the developer. Ground your explanation in the reference
material above wherever possible - do not invent security claims that are not
supported by it. If the finding looks like a plausible false positive given the
context, say so honestly and lower your confidence score accordingly.

{format_instructions}
""",
    input_variables=[
        "title", "swc_id", "severity", "source_tool", "contract_file",
        "line_start", "line_end", "raw_description", "retrieved_context",
    ],
    partial_variables={"format_instructions": parser.get_format_instructions()},
)
