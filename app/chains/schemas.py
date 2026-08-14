"""Pydantic models used for structured LLM output and API responses."""
from __future__ import annotations

from pydantic import BaseModel, Field


class RawFinding(BaseModel):
    id: str
    check: str
    title: str
    description: str
    severity: str
    confidence: str
    swc_id: str | None = None
    elements: list[dict] = Field(default_factory=list)


class ExplainedFinding(BaseModel):
    """Structured output the LLM must produce for each finding."""

    finding_id: str = Field(description="The id of the finding being explained")
    plain_explanation: str = Field(
        description="2-4 sentence plain-English explanation of the vulnerability, "
        "written for a junior developer with no security background."
    )
    why_it_matters: str = Field(
        description="1-2 sentences on the real-world impact / exploit scenario."
    )
    severity: str = Field(description="Critical | High | Medium | Low | Informational")
    fix_snippet: str = Field(
        description="A short Solidity code snippet or concrete step showing the fix."
    )
    references: list[str] = Field(
        default_factory=list, description="SWC IDs or doc references used as grounding"
    )


class AuditReport(BaseModel):
    target: str
    total_findings: int
    findings: list[ExplainedFinding]
