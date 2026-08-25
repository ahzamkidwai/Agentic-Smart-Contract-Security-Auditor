"""
app/services/workspace_manager.py

Handles per-job workspace creation, safe zip extraction, and teardown.
Both the zip-upload and GitHub flows land their source into the same
workspace/{job_id}/ layout so everything downstream (project_type.py,
slither_project_runner.py) is source-agnostic.

Destination: app/services/workspace_manager.py
Runs under: .venv-app

Windows/MinGW note (per your existing footgun list): all paths returned to
callers that will be embedded in subprocess JSON payloads to .venv-analysis
are normalized with cygpath -m style forward slashes, matching your existing
pattern in slither_runner.py. Do NOT use os.path on Windows and assume
backslashes are safe to hand to the analysis-side subprocess.
"""

import os
import shutil
import sys
import uuid
import zipfile
from pathlib import Path

WORKSPACE_ROOT = Path(os.environ.get("AUDIT_WORKSPACE_ROOT", "workspace")).resolve()
MAX_EXTRACTED_BYTES = 500 * 1024 * 1024   # 500 MB uncompressed guard
MAX_FILE_COUNT = 5000


class WorkspaceError(Exception):
    pass


def new_job_workspace() -> tuple[str, Path]:
    """Create and return (job_id, workspace_dir)."""
    job_id = uuid.uuid4().hex
    job_dir = WORKSPACE_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    return job_id, job_dir


def _safe_extract_path(job_dir: Path, member_name: str) -> Path:
    """
    Resolve a zip member path against job_dir and refuse anything that
    escapes it (zip-slip protection). Raises WorkspaceError on violation.
    """
    target = (job_dir / member_name).resolve()
    if not str(target).startswith(str(job_dir.resolve())):
        raise WorkspaceError(f"Rejected unsafe path in archive: {member_name}")
    return target


def extract_zip(zip_path: Path, job_dir: Path) -> Path:
    """
    Safely extract an uploaded zip into job_dir/source/.
    Returns the path to the extracted project root, attempting to collapse
    a common single top-level folder (common when users zip a repo folder
    directly, e.g. "my-project/contracts/..." rather than "contracts/...").
    """
    source_dir = job_dir / "source"
    source_dir.mkdir(exist_ok=True)

    total_size = 0
    file_count = 0

    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            file_count += 1
            total_size += info.file_size
            if file_count > MAX_FILE_COUNT:
                raise WorkspaceError(f"Archive exceeds {MAX_FILE_COUNT} files")
            if total_size > MAX_EXTRACTED_BYTES:
                raise WorkspaceError("Archive exceeds uncompressed size limit")

            dest = _safe_extract_path(source_dir, info.filename)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)

    return _collapse_single_root(source_dir)


def _collapse_single_root(source_dir: Path) -> Path:
    """If source_dir contains exactly one subdirectory and nothing else,
    treat that as the actual project root (handles 'zip of a folder')."""
    entries = list(source_dir.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return source_dir


def to_windows_path(p: Path) -> str:
    """
    Mirrors your existing cygpath -m convention: forward-slash Windows paths,
    safe to embed directly in JSON payloads sent to .venv-analysis subprocess
    calls. On non-Windows this is a no-op passthrough (posix path as-is).
    """
    if sys.platform == "win32":
        # Equivalent to `cygpath -m` when running under Git Bash; here we're
        # already in native Windows Python so just normalize slashes.
        return str(p.resolve()).replace("\\", "/")
    return str(p.resolve())


def cleanup_job(job_dir: Path) -> None:
    """Delete a job workspace. Call after report generation + TTL, or on
    failure, so uploaded/cloned third-party code doesn't linger on disk."""
    shutil.rmtree(job_dir, ignore_errors=True)


def cleanup_stale_jobs(max_age_hours: int = 24) -> int:
    """Sweep workspace/ for job dirs older than max_age_hours. Wire this up
    as a periodic task (APScheduler / cron) rather than calling ad hoc."""
    import time
    removed = 0
    cutoff = time.time() - max_age_hours * 3600
    if not WORKSPACE_ROOT.exists():
        return 0
    for entry in WORKSPACE_ROOT.iterdir():
        if entry.is_dir() and entry.stat().st_mtime < cutoff:
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
    return removed