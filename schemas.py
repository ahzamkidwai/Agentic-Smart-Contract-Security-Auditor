"""
Shared Pydantic models used across analyzers, rag, report, and api layers.
This is the single source of truth for what a "Finding" looks like -
it's what lets Slither and Mythril's very different output formats
become interchangeable everywhere downstream.
"""
from enum import Enum
from typing import Optional
from pydantic import BaseModel


class Severity(str, Enum):
    critical = "Critical"
    high = "High"
    medium = "Medium"
    low = "Low"
    informational = "Informational"
    optimization = "Optimization"


class Finding(BaseModel):
    source_tool: str                    # "slither" | "mythril"
    swc_id: Optional[str] = None
    title: str
    severity: Severity
    contract_file: str
    contract_name: Optional[str] = None
    line_start: int
    line_end: int
    raw_description: str


class ExplainedFinding(Finding):
    plain_explanation: str
    exploit_scenario: str
    fix_snippet: str
    confidence: float
    sources: list[str] = []
