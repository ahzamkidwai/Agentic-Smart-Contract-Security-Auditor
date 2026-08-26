"""FastAPI routes tying the whole pipeline together:

Slither (subprocess) -> normalize findings -> RAG explainer chain ->
SQLModel persistence -> WeasyPrint PDF.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.analyzers.slither_runner import (
    SlitherExecutionError,
    normalize_findings,
    run_slither,
)
from app.chains.explainer_chain import explain_findings
from app.chains.schemas import AuditReport, RawFinding
from app.db import engine
from app.models.db_models import AuditRun, FindingRecord
from app.reports.pdf_report import render_pdf
from app.services.source_reading import (
    primary_location,
    read_contract_header,
    read_full_source,
)

router = APIRouter()

# Where pasted-contract text gets written to disk before being handed to
# the exact same slither -> explain -> persist pipeline used for
# path-based audits. Kept separate from `workspace/` (project uploads) so
# the two flows can be cleaned up independently.
_PASTE_WORKSPACE_ROOT = Path("workspace") / "pasted"
_PASTE_WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

# Only allow a safe, predictable filename — never trust user input for a
# path component that gets joined onto disk.
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}\.sol$")
_MAX_PASTE_BYTES = 300_000


def _read_contract_header(target_path: str, max_lines: int = 30) -> str:
    """
    Read the first ``max_lines`` lines of a Solidity file.

    Returns an empty string if target_path is a directory or cannot be read.
    This gives the LLM visibility into pragma, imports, and contract-level
    declarations so it can detect already-applied fixes (e.g. ReentrancyGuard
    already imported) without falsely recommending them again.
    """
    from pathlib import Path

    p = Path(target_path)
    if not p.is_file() or p.suffix.lower() != ".sol":
        return ""
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[:max_lines])
    except Exception:
        return ""


_MAX_FULL_SOURCE_BYTES = 300_000  # sanity cap; typical contracts are a few KB


def _read_full_source(target_path: str) -> str:
    """
    Read the entire contract file for deterministic, programmatic checks
    (e.g. compiler_analysis's regex trigger-condition matching) — this is
    NOT injected into the LLM prompt wholesale, only used in Python logic,
    so there's no prompt-size concern; only a sanity cap against
    pathological input.
    """
    from pathlib import Path

    p = Path(target_path)
    if not p.is_file() or p.suffix.lower() != ".sol":
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        return text[:_MAX_FULL_SOURCE_BYTES]
    except Exception:
        return ""




class AuditRequest(BaseModel):
    target_path: str  # absolute path to a .sol file or project directory


class AuditRunOut(BaseModel):
    id: int
    target: str
    status: str
    total_findings: int


@router.post("/audit", response_model=AuditRunOut, status_code=202)
def start_audit(req: AuditRequest, background_tasks: BackgroundTasks):
    """Kicks off an async audit run and returns immediately with a run id."""
    with Session(engine) as session:
        run = AuditRun(target=req.target_path, status="pending")
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    background_tasks.add_task(_process_audit, run_id, req.target_path)
    return AuditRunOut(id=run_id, target=req.target_path, status="pending", total_findings=0)


class PasteAuditRequest(BaseModel):
    code: str = Field(..., description="Raw Solidity source pasted by the user")
    filename: str = Field(
        default="PastedContract.sol",
        description="Display filename. Must end in .sol; only used for the on-disk temp file and the report.",
    )


@router.post("/audit/paste", response_model=AuditRunOut, status_code=202)
def start_paste_audit(req: PasteAuditRequest, background_tasks: BackgroundTasks):
    """
    Same pipeline as POST /audit (Slither -> RAG explainer -> persistence),
    but for a contract pasted directly into the UI instead of a path that
    already exists on disk: the code is written to a private temp file
    first, then handed to the identical _process_audit() used everywhere
    else, so single-file and pasted-code audits get exactly the same
    grounding/anti-hallucination guarantees.
    """
    code = req.code
    if not code or not code.strip():
        raise HTTPException(400, "code must not be empty")
    if len(code.encode("utf-8")) > _MAX_PASTE_BYTES:
        raise HTTPException(400, f"code exceeds {_MAX_PASTE_BYTES} byte limit")

    filename = req.filename.strip() or "PastedContract.sol"
    if not filename.lower().endswith(".sol"):
        filename += ".sol"
    if not _SAFE_FILENAME_RE.match(filename):
        # Fall back to a safe generated name rather than rejecting the
        # request outright over a cosmetic filename choice.
        filename = "PastedContract.sol"

    job_dir = _PASTE_WORKSPACE_ROOT / uuid.uuid4().hex
    job_dir.mkdir(parents=True, exist_ok=True)
    target_path = job_dir / filename
    target_path.write_text(code, encoding="utf-8")

    with Session(engine) as session:
        run = AuditRun(target=str(target_path), status="pending")
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    background_tasks.add_task(_process_audit, run_id, str(target_path))
    return AuditRunOut(id=run_id, target=filename, status="pending", total_findings=0)


def _process_audit(run_id: int, target_path: str) -> None:
    with Session(engine) as session:
        run = session.get(AuditRun, run_id)
        run.status = "running"
        session.add(run)
        session.commit()

    try:
        raw_output = run_slither(target_path)
        normalized = normalize_findings(raw_output, target_path=target_path)

        # Read the first 30 lines of the contract so the LLM can detect
        # already-applied fixes (imports, modifiers, pragma version).
        contract_header = read_contract_header(target_path, max_lines=30)
        full_source = read_full_source(target_path)

        raw_findings = [
            RawFinding(**{**f, "contract_header": contract_header, "full_source": full_source})
            for f in normalized
        ]

        explained = explain_findings(raw_findings)

        report = AuditReport(
            target=target_path,
            total_findings=len(explained),
            findings=explained,
        )
        pdf_path = render_pdf(report, output_filename=f"audit_run_{run_id}.pdf")

        with Session(engine) as session:
            run = session.get(AuditRun, run_id)
            run.status = "done"
            run.total_findings = len(explained)
            run.report_pdf_path = pdf_path
            run.completed_at = datetime.now(timezone.utc)
            session.add(run)

            for raw, exp in zip(raw_findings, explained):
                # Location comes straight from Slither's own source_mapping
                # (raw.elements), never from the LLM's narration — this is
                # what lets the UI show an exact file+line without any risk
                # of the model inventing or mis-stating one.
                loc = primary_location(raw.elements)
                session.add(
                    FindingRecord(
                        audit_run_id=run_id,
                        finding_id=raw.id,
                        check=raw.check,
                        severity=exp.severity,
                        swc_id=raw.swc_id,
                        file_name=loc["file"],
                        start_line=loc["start_line"],
                        end_line=loc["end_line"],
                        raw_description=raw.description,
                        plain_explanation=exp.plain_explanation,
                        why_it_matters=exp.why_it_matters,
                        fix_snippet=exp.fix_snippet,
                        fix_already_present=exp.fix_already_present,
                        references=exp.references,
                        related_finding_ids=exp.related_finding_ids,
                        severity_rationale=exp.severity_rationale,
                        applicability_note=exp.applicability_note,
                        evidence=exp.evidence,
                    )
                )
            session.commit()

    except SlitherExecutionError as e:
        _mark_failed(run_id, str(e))
    except Exception as e:  # noqa: BLE001 - surface any pipeline error onto the run record
        _mark_failed(run_id, f"Unexpected error: {e}")


def _mark_failed(run_id: int, message: str) -> None:
    with Session(engine) as session:
        run = session.get(AuditRun, run_id)
        run.status = "failed"
        run.error_message = message
        session.add(run)
        session.commit()


@router.get("/audit/{run_id}")
def get_audit(run_id: int):
    with Session(engine) as session:
        run = session.get(AuditRun, run_id)
        if not run:
            raise HTTPException(404, "Audit run not found")
        findings = session.exec(
            select(FindingRecord).where(FindingRecord.audit_run_id == run_id)
        ).all()
        return {
            "id": run.id,
            "target": run.target,
            "status": run.status,
            "total_findings": run.total_findings,
            "error_message": run.error_message,
            "findings": [f.dict() for f in findings],
        }


@router.get("/audit/{run_id}/report.pdf")
def get_report_pdf(run_id: int):
    with Session(engine) as session:
        run = session.get(AuditRun, run_id)
        if not run or run.status != "done" or not run.report_pdf_path:
            raise HTTPException(404, "Report not ready or not found")
        pdf_path = run.report_pdf_path
    return FileResponse(pdf_path, media_type="application/pdf")