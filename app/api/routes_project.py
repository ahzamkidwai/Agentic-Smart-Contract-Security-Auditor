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
from app.services.github_fetch import clone_repo, WorkspaceError as GitHubError
from app.services.project_type import detect_project_type, find_project_root, install_dependencies, find_solidity_files, ProjectType

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
        "files": [
            {
                "relative_path": f.relative_path,
                "findings_count": f.findings_count,
                "highest_severity": f.highest_severity,
                "report_url": f"/audit/project/{job_id}/files/{f.id}/report.pdf" if f.pdf_path else None,
            }
            for f in files
        ],
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
        for sol_path in sol_files:
            relative_path = sol_path.resolve().relative_to(contract_root.resolve()).as_posix()
            raw_findings = grouped_findings.get(relative_path, [])

            af = AuditFile(job_id=job.id, relative_path=relative_path, findings_count=len(raw_findings))
            total_findings += len(raw_findings)

            # TODO: run your existing per-finding pipeline here, scoped to this file
            # (skip straight to af.highest_severity = None / pdf_path = None
            # when raw_findings is empty - no need to hit the RAG/LLM chain
            # for a file with nothing to explain):
            #   if raw_findings:
            #       applicable = applicability.filter(raw_findings)
            #       correlated = correlation.link(applicable, project_wide_context=grouped_findings)
            #       scored = severity.score(correlated)
            #       explained = rag_explain(scored)   # FAISS + Groq/Gemini, same as single-file path
            #       fixes = autofix.generate(explained)
            #       af.pdf_path = render_pdf(explained, fixes, out=job_dir / "reports" / f"{relative_path}.pdf")
            #       af.highest_severity = max(f.severity for f in scored)

            session.add(af)

        # Any finding whose primary element pointed to a file outside
        # sol_files (shouldn't normally happen, but _group_by_file() has an
        # "_unattributed" bucket as a safety net) - surface it rather than
        # silently dropping those findings from total_findings.
        leftover = set(grouped_findings.keys()) - {
            p.resolve().relative_to(contract_root.resolve()).as_posix() for p in sol_files
        }
        for relative_path in leftover:
            raw_findings = grouped_findings[relative_path]
            af = AuditFile(job_id=job.id, relative_path=relative_path, findings_count=len(raw_findings))
            total_findings += len(raw_findings)
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