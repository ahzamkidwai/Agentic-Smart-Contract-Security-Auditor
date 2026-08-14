"""FastAPI routes tying the whole pipeline together:

Slither (subprocess) -> normalize findings -> RAG explainer chain ->
SQLModel persistence -> WeasyPrint PDF.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
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

router = APIRouter()


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


def _process_audit(run_id: int, target_path: str) -> None:
    with Session(engine) as session:
        run = session.get(AuditRun, run_id)
        run.status = "running"
        session.add(run)
        session.commit()

    try:
        raw_output = run_slither(target_path)
        normalized = normalize_findings(raw_output)
        raw_findings = [RawFinding(**f) for f in normalized]

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
                session.add(
                    FindingRecord(
                        audit_run_id=run_id,
                        finding_id=raw.id,
                        check=raw.check,
                        severity=exp.severity,
                        swc_id=raw.swc_id,
                        raw_description=raw.description,
                        plain_explanation=exp.plain_explanation,
                        why_it_matters=exp.why_it_matters,
                        fix_snippet=exp.fix_snippet,
                        references=exp.references,
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
