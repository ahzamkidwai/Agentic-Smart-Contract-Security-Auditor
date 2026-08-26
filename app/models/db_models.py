"""SQLModel persistence layer for audit runs and their findings."""
from datetime import datetime, timezone
from typing import List, Optional

from sqlmodel import JSON, Column, Field, Relationship, SQLModel


class AuditRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    target: str
    status: str = Field(default="pending")  # pending | running | done | failed
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    total_findings: int = 0
    report_pdf_path: Optional[str] = None
    error_message: Optional[str] = None

    findings: List["FindingRecord"] = Relationship(back_populates="audit_run")


class FindingRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    audit_run_id: Optional[int] = Field(default=None, foreign_key="auditrun.id")

    finding_id: str
    check: str
    severity: str
    swc_id: Optional[str] = None

    # --- Exact location, extracted deterministically from Slither's own
    # source_mapping (never from LLM narration) so file/line display can
    # never hallucinate a location. ---
    file_name: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None

    raw_description: str
    plain_explanation: str
    why_it_matters: str
    fix_snippet: str
    fix_already_present: bool = False
    references: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    related_finding_ids: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    severity_rationale: str = ""
    applicability_note: str = ""
    # Deterministic facts (line numbers, variable names, call ordering)
    # pulled straight from Slither's output — see ExplainedFinding.evidence.
    evidence: dict = Field(default_factory=dict, sa_column=Column(JSON))

    audit_run: Optional[AuditRun] = Relationship(back_populates="findings")