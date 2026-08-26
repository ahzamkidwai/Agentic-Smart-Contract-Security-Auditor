"""
app/api/routes_project.py

New endpoints:
    POST /audit/project/upload   - multipart zip upload of a Hardhat/Foundry repo
    POST /audit/project/github   - {"github_url": "...", "ref": "main"}
    GET  /audit/project/{job_id} - poll job status
    GET  /audit/project/{job_id}/files - per-file findings summary (for
                                          your "generate response file-wise"
                                          requirement)
    GET  /audit/project/{job_id}/files/{file_id}/report.pdf

Destination: app/api/routes_project.py
Runs under: .venv-app

Wire-up needed in main.py:
    from app.api.routes_project import router as project_router
    app.include_router(project_router)

This is a prototype: the heavy steps (install deps, run slither, RAG-explain,
render PDFs) are stubbed as a single synchronous `_process_job()` call for
clarity. In practice you'll want this on a background task queue
(FastAPI BackgroundTasks at minimum, or Celery/RQ if you want retry/queueing)
since a full project run can take minutes.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from sqlmodel import Session, select

# Uses the same engine your existing app/db.py already creates for init_db().
# If your engine variable is named differently, adjust this one import line.
from app.db import engine

from app.models.project import AuditJob, AuditFile, SourceType, ProjectType as DBProjectType, JobStatus
from app.services.workspace_manager import (
    new_job_workspace, extract_zip, cleanup_job, to_windows_path, WorkspaceError,
)
from app.services.github_fetch import clone_repo, strip_git_dir, WorkspaceError as GitHubError
from app.services.project_type import detect_project_type, find_project_root, install_dependencies, find_solidity_files, ProjectType
from app.services.source_reading import primary_location, read_contract_header, read_full_source
from app.analyzers.slither_runner import normalize_findings
from app.chains.explainer_chain import explain_findings
from app.chains.schemas import RawFinding

router = APIRouter(prefix="/audit/project", tags=["project-audit"])

# Resolved relative to THIS FILE's location, not the process's current
# working directory - subprocess.run() doesn't care where you launched
# uvicorn from, but a hardcoded "../..." relative path does, and uvicorn's
# cwd is your repo root (audit-explainer/), not one level above it. That
# mismatch is what caused a WinError 2 here even after forge/npm resolution
# was fixed - this path was silently pointing outside the project entirely.
_REPO_ROOT = Path(__file__).resolve().parents[2]  # app/api/routes_project.py -> repo root

if sys.platform == "win32":
    VENV_ANALYSIS_PYTHON = _REPO_ROOT / ".venv-analysis" / "Scripts" / "python.exe"
else:
    VENV_ANALYSIS_PYTHON = _REPO_ROOT / ".venv-analysis" / "bin" / "python"

SLITHER_PROJECT_SCRIPT = _REPO_ROOT / "analysis" / "slither_project_runner.py"


def _check_analysis_env() -> None:
    """Fail fast with a clear error before ever invoking subprocess, rather
    than letting a bad path surface as an opaque WinError 2 deep in a
    background task."""
    if not VENV_ANALYSIS_PYTHON.exists():
        raise WorkspaceError(
            f".venv-analysis Python interpreter not found at "
            f"{VENV_ANALYSIS_PYTHON}. Confirm .venv-analysis exists at the "
            f"repo root ({_REPO_ROOT})."
        )
    if not SLITHER_PROJECT_SCRIPT.exists():
        raise WorkspaceError(
            f"slither_project_runner.py not found at {SLITHER_PROJECT_SCRIPT}."
        )


def get_session():
    """
    Request-scoped session for the route handlers themselves. Do NOT pass
    this session into background_tasks.add_task() - FastAPI closes it right
    after the request returns, before the background task runs. Background
    work opens its own session via _session_for_background() instead.
    """
    with Session(engine) as session:
        yield session


def _session_for_background() -> Session:
    """A fresh, independently-scoped session for use inside background
    tasks, which outlive the request that triggered them."""
    return Session(engine)


@router.post("/upload")
async def upload_project(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    job_id, job_dir = new_job_workspace()

    tmp_zip = Path(tempfile.gettempdir()) / f"{job_id}.zip"
    with open(tmp_zip, "wb") as f:
        f.write(await file.read())

    job = AuditJob(job_id=job_id, source_type=SourceType.ZIP_UPLOAD, status=JobStatus.EXTRACTING)
    session.add(job)
    session.commit()
    session.refresh(job)

    try:
        project_root = extract_zip(tmp_zip, job_dir)
    except WorkspaceError as e:
        job.status = JobStatus.FAILED
        job.error_message = str(e)
        session.add(job)
        session.commit()
        cleanup_job(job_dir)
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        tmp_zip.unlink(missing_ok=True)

    background_tasks.add_task(_process_job, job.id, job_dir, project_root)
    return {"job_id": job_id, "status": job.status}


@router.post("/github")
async def audit_github_repo(
    github_url: str,
    ref: str = "main",
    background_tasks: BackgroundTasks = None,
    session: Session = Depends(get_session),
):
    job_id, job_dir = new_job_workspace()

    job = AuditJob(
        job_id=job_id, source_type=SourceType.GITHUB_URL,
        github_url=github_url, github_ref=ref, status=JobStatus.EXTRACTING,
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    try:
        project_root = clone_repo(github_url, job_dir, ref=ref)
    except GitHubError as e:
        job.status = JobStatus.FAILED
        job.error_message = str(e)
        session.add(job)
        session.commit()
        cleanup_job(job_dir)
        raise HTTPException(status_code=400, detail=str(e))

    background_tasks.add_task(_process_job, job.id, job_dir, project_root)
    return {"job_id": job_id, "status": job.status}


@router.get("/{job_id}")
def get_job_status(job_id: str, session: Session = Depends(get_session)):
    job = session.exec(select(AuditJob).where(AuditJob.job_id == job_id)).first()
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.get("/{job_id}/files")
def list_job_files(job_id: str, session: Session = Depends(get_session)):
    """Per-file findings summary - this is your 'file-wise response' view."""
    job = session.exec(select(AuditJob).where(AuditJob.job_id == job_id)).first()
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    files = session.exec(select(AuditFile).where(AuditFile.job_id == job.id)).all()
    return {
        "job_id": job_id,
        "status": job.status,
        "total_files": job.total_files,
        "total_findings": job.total_findings,
        "error_message": job.error_message,
        "files": [
            {
                "id": f.id,
                "relative_path": f.relative_path,
                "findings_count": f.findings_count,
                "highest_severity": f.highest_severity,
                "report_url": f"/audit/project/{job_id}/files/{f.id}/report.pdf" if f.pdf_path else None,
            }
            for f in files
        ],
    }


@router.get("/{job_id}/files/{file_id}")
def get_job_file_findings(job_id: str, file_id: int, session: Session = Depends(get_session)):
    """
    Full per-finding detail for a single file: file name + exact line
    numbers (taken directly from Slither's source_mapping, not the LLM),
    plain-English explanation, why it matters, fix snippet, SWC references,
    and the deterministic evidence/applicability notes that ground the
    explanation and guard against hallucination.
    """
    job = session.exec(select(AuditJob).where(AuditJob.job_id == job_id)).first()
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    af = session.get(AuditFile, file_id)
    if not af or af.job_id != job.id:
        raise HTTPException(status_code=404, detail="file not found in this job")
    return {
        "job_id": job_id,
        "relative_path": af.relative_path,
        "findings_count": af.findings_count,
        "highest_severity": af.highest_severity,
        "findings": af.findings_json,
    }


def _process_job(job_db_id: int, job_dir: Path, project_root: Path):
    """
    Background pipeline. Prototype-level: sequential, no retries.
    Replace the TODO blocks with your existing applicability.py /
    correlation.py / severity.py / RAG-explain / autofix.py / PDF render
    calls, scoped per file instead of per single upload.

    Opens its own DB session (_session_for_background) rather than reusing
    the route's request-scoped session, which FastAPI closes as soon as the
    request returns - before this background task gets to run.
    """
    session = _session_for_background()
    job = session.get(AuditJob, job_db_id)
    try:
        project_type, contract_root = find_project_root(project_root)
        job.project_type = DBProjectType(project_type.value) if project_type != ProjectType.UNKNOWN else DBProjectType.UNKNOWN
        session.add(job); session.commit()

        sol_files = find_solidity_files(contract_root)
        job.total_files = len(sol_files)

        job.status = JobStatus.INSTALLING_DEPS
        session.add(job); session.commit()
        install_dependencies(contract_root, project_type)
        if job.source_type == SourceType.GITHUB_URL:
            strip_git_dir(contract_root)

        job.status = JobStatus.RUNNING_SLITHER
        session.add(job); session.commit()

        _check_analysis_env()

        payload = json.dumps({
            "project_root": to_windows_path(contract_root),
            "project_type": project_type.value,
            "solc_version": job.solc_version,  # None -> runner uses currently selected version
        })
        result = subprocess.run(
            [str(VENV_ANALYSIS_PYTHON), str(SLITHER_PROJECT_SCRIPT), payload],
            capture_output=True, text=True, timeout=320,
        )

        # Diagnose BEFORE attempting json.loads - an empty/non-JSON stdout
        # almost always means the .venv-analysis subprocess raised an
        # exception or printed a traceback to stderr instead of returning
        # the expected JSON contract. Surface that directly rather than
        # letting json.loads() fail with an opaque "Expecting value" error.
        if not result.stdout.strip():
            raise WorkspaceError(
                f"slither_project_runner.py produced no stdout "
                f"(exit code {result.returncode}). "
                f"stderr:\n{result.stderr.strip()[:2000]}"
            )

        try:
            analysis_output = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise WorkspaceError(
                f"slither_project_runner.py did not return valid JSON "
                f"(exit code {result.returncode}). Parse error: {e}\n"
                f"stdout (first 500 chars): {result.stdout[:500]!r}\n"
                f"stderr (first 2000 chars):\n{result.stderr.strip()[:2000]}"
            )

        if not analysis_output.get("success"):
            raise WorkspaceError(f"Slither project run failed: {analysis_output.get('error')}")

        job.status = JobStatus.EXPLAINING
        session.add(job); session.commit()

        grouped_findings = analysis_output["files"]  # {relative_path: [raw findings]}

        # Flatten every file's raw Slither detector dicts back into one list
        # so normalize_findings() can run its existing, single-file-tested
        # logic (annotated >>>-flagged source blocks, SWC mapping, id
        # assignment) exactly once for the whole project. target_path is
        # the project root, so normalize_findings resolves each element's
        # relative filename against it correctly regardless of which file
        # it came from.
        _SEVERITY_ORDER = ["Critical", "High", "Medium", "Low", "Informational", "Optimization"]

        all_raw_detectors = [d for detectors in grouped_findings.values() for d in detectors]
        normalized = normalize_findings(
            {"results": {"detectors": all_raw_detectors}}, target_path=str(contract_root)
        )

        raw_findings: list[RawFinding] = []
        for f in normalized:
            loc = primary_location(f["elements"])
            file_path = (contract_root / loc["file"]) if loc["file"] else None
            header = read_contract_header(file_path) if file_path else ""
            full_source = read_full_source(file_path) if file_path else ""
            raw_findings.append(
                RawFinding(**{**f, "contract_header": header, "full_source": full_source})
            )

        # RAG-explain the whole project's findings in one batch so
        # correlation.py can link related findings across the project
        # (e.g. the same call site flagged by two detectors), same
        # grounding/temperature-0/evidence guardrails as the single-file
        # pipeline (see app/chains/explainer_chain.py).
        explained = explain_findings(raw_findings) if raw_findings else []

        # Group explained findings by their primary file so each AuditFile
        # gets exactly the findings that live in it, with exact
        # file+line location taken from Slither's own source_mapping.
        by_relative_path: dict[str, list[dict]] = {}
        for raw, exp in zip(raw_findings, explained):
            loc = primary_location(raw.elements)
            relative_path = loc["file"] or "_unattributed"
            by_relative_path.setdefault(relative_path, []).append(
                {
                    "finding_id": raw.id,
                    "check": raw.check,
                    "title": raw.title,
                    "severity": exp.severity,
                    "confidence": raw.confidence,
                    "swc_id": raw.swc_id,
                    "file_name": loc["file"],
                    "start_line": loc["start_line"],
                    "end_line": loc["end_line"],
                    "flagged_lines": loc["lines"],
                    "plain_explanation": exp.plain_explanation,
                    "why_it_matters": exp.why_it_matters,
                    "fix_snippet": exp.fix_snippet,
                    "fix_already_present": exp.fix_already_present,
                    "references": exp.references,
                    "related_finding_ids": exp.related_finding_ids,
                    "severity_rationale": exp.severity_rationale,
                    "applicability_note": exp.applicability_note,
                    "evidence": exp.evidence,
                }
            )

        # Build the AuditFile rows from the FULL file list (sol_files, which
        # produced total_files above), not from grouped_findings.keys(). A
        # file with zero Slither findings never gets a key in grouped_findings
        # at all - iterating that dict instead of sol_files was why clean
        # files (SettlementMath.sol, test/script files, etc.) silently
        # disappeared from /files even though total_files correctly counted
        # all 6. Every scanned file should appear in the report, including
        # ones with 0 findings - that's expected audit-report behavior, not
        # an omission.
        total_findings = 0
        seen_paths: set[str] = set()
        for sol_path in sol_files:
            relative_path = sol_path.resolve().relative_to(contract_root.resolve()).as_posix()
            seen_paths.add(relative_path)
            file_findings = by_relative_path.get(relative_path, [])

            highest = None
            for sev in _SEVERITY_ORDER:
                if any(fnd["severity"] == sev for fnd in file_findings):
                    highest = sev
                    break

            af = AuditFile(
                job_id=job.id,
                relative_path=relative_path,
                findings_count=len(file_findings),
                highest_severity=highest,
                findings_json=file_findings,
            )
            total_findings += len(file_findings)
            session.add(af)

        # Any finding whose primary element pointed to a file outside
        # sol_files (shouldn't normally happen, but _group_by_file() has an
        # "_unattributed" bucket as a safety net) - surface it rather than
        # silently dropping those findings from total_findings.
        for relative_path, file_findings in by_relative_path.items():
            if relative_path in seen_paths:
                continue
            highest = None
            for sev in _SEVERITY_ORDER:
                if any(fnd["severity"] == sev for fnd in file_findings):
                    highest = sev
                    break
            af = AuditFile(
                job_id=job.id,
                relative_path=relative_path,
                findings_count=len(file_findings),
                highest_severity=highest,
                findings_json=file_findings,
            )
            total_findings += len(file_findings)
            session.add(af)

        job.total_findings = total_findings
        job.status = JobStatus.DONE
        session.add(job); session.commit()

    except Exception as e:
        job.status = JobStatus.FAILED
        job.error_message = str(e)[:2000]
        session.add(job); session.commit()
    finally:
        session.close()
    # Deliberately NOT calling cleanup_job() here - keep workspace for report
    # download, and sweep it later via workspace_manager.cleanup_stale_jobs()
    # on a periodic task.