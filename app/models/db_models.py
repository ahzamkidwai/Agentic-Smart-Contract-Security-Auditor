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

    raw_description: str
    plain_explanation: str
    why_it_matters: str
    fix_snippet: str
    references: List[str] = Field(default_factory=list, sa_column=Column(JSON))

    audit_run: Optional[AuditRun] = Relationship(back_populates="findings")
