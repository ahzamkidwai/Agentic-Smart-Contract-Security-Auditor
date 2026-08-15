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
    # Actual Solidity source lines that Slither flagged (may be empty if
    # source_mapping was unavailable or the file could not be read).
    source_lines: str = ""
    # First ~30 lines of the contract file (imports, pragma, contract
    # declaration) so the LLM can detect already-applied fixes such as
    # `import "@openzeppelin/contracts/security/ReentrancyGuard.sol"`.
    contract_header: str = ""


class ExplainedFinding(BaseModel):
    """Structured output the LLM must produce for each finding."""

    finding_id: str = Field(description="The id of the finding being explained")
    plain_explanation: str = Field(
        description=(
            "2-4 sentence plain-English explanation of the vulnerability "
            "written for a junior developer with no security background. "
            "Base it only on the flagged source lines and SWC context provided — "
            "do not invent behaviour that isn't shown in the code."
        )
    )
    why_it_matters: str = Field(
        description="1-2 sentences on the real-world impact / exploit scenario."
    )
    severity: str = Field(description="Critical | High | Medium | Low | Informational")
    fix_snippet: str = Field(
        description=(
            "A concrete Solidity patch to the *flagged lines* shown above. "
            "If the fix is already present in the contract (e.g. the header "
            "already imports ReentrancyGuard and uses the nonReentrant modifier), "
            "write 'Fix already applied: <reason>' instead of repeating it."
        )
    )
    fix_already_present: bool = Field(
        default=False,
        description=(
            "Set to true if the contract's imports or the flagged source lines "
            "already contain the standard mitigation for this finding "
            "(e.g. the nonReentrant modifier is present, SafeMath is imported, "
            "Solidity >=0.8 is declared for an overflow finding, etc.)."
        ),
    )
    references: list[str] = Field(
        default_factory=list,
        description=(
            "SWC IDs that directly describe this finding. "
            "Only include IDs that appear in the retrieved SWC context. "
            "Leave empty if the context is irrelevant or no match exists."
        ),
    )


class AuditReport(BaseModel):
    target: str
    total_findings: int
    findings: list[ExplainedFinding]
