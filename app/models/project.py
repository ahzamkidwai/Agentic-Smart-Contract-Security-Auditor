"""
app/models/project.py

New SQLModel tables to support folder/GitHub project-wide audits.

Destination: app/models/project.py
Runs under: .venv-app (SQLModel/FastAPI stack)

Wire-up needed in your existing db.py / main.py:
    from app.models.project import AuditJob, AuditFile
    SQLModel.metadata.create_all(engine)   # or add to your existing migration script

If you already have a `Finding` table from the single-file pipeline, add a
nullable `file_id: Optional[int] = Field(default=None, foreign_key="auditfile.id")`
column to it via your migration script (do NOT redefine Finding here — this file
assumes it already exists elsewhere and only adds the FK relationship note below).
"""

from datetime import datetime
from enum import Enum
from typing import Optional, List

from sqlmodel import JSON, Column, SQLModel, Field, Relationship


class SourceType(str, Enum):
    ZIP_UPLOAD = "zip_upload"
    GITHUB_URL = "github_url"


class ProjectType(str, Enum):
    HARDHAT = "hardhat"
    FOUNDRY = "foundry"
    UNKNOWN = "unknown"


class JobStatus(str, Enum):
    PENDING = "pending"
    EXTRACTING = "extracting"       # unzip or git clone in progress
    INSTALLING_DEPS = "installing_deps"
    COMPILING = "compiling"
    RUNNING_SLITHER = "running_slither"
    EXPLAINING = "explaining"       # RAG + LLM pass over findings
    RENDERING = "rendering"         # PDF generation
    DONE = "done"
    FAILED = "failed"


class AuditJob(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: str = Field(index=True, unique=True)  # uuid4 hex, also the workspace dir name

    source_type: SourceType
    github_url: Optional[str] = None
    github_ref: Optional[str] = None            # branch/tag/commit, default "main"

    project_type: ProjectType = ProjectType.UNKNOWN
    status: JobStatus = JobStatus.PENDING
    error_message: Optional[str] = None

    solc_version: Optional[str] = None           # resolved by solc-select step
    total_files: int = 0
    total_findings: int = 0
    highest_severity: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    files: List["AuditFile"] = Relationship(back_populates="job")


class AuditFile(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="auditjob.id", index=True)

    relative_path: str                            # e.g. "contracts/VulnerableBank.sol"
    findings_count: int = 0
    highest_severity: Optional[str] = None
    pdf_path: Optional[str] = None                 # path to per-file rendered report

    # Full per-finding detail for this file: list of dicts, each with
    # finding_id, check, severity, swc_id, file_name, start_line, end_line,
    # plain_explanation, why_it_matters, fix_snippet, fix_already_present,
    # references, evidence, severity_rationale, applicability_note,
    # related_finding_ids. Populated once by _process_job (see
    # routes_project.py); empty list for files with zero findings.
    findings_json: List[dict] = Field(default_factory=list, sa_column=Column(JSON))

    job: Optional[AuditJob] = Relationship(back_populates="files")

    # NOTE: your existing Finding table should get a nullable file_id FK
    # pointing here, so a finding belongs optionally to an AuditFile when
    # it came from a project-wide run, and is None for single-file runs.