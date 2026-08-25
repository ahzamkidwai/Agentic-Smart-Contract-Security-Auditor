"""
app/services/github_fetch.py

Shallow-clones a GitHub repo into a job workspace. Lands the repo at the
same workspace/{job_id}/source/ location that extract_zip() uses, so
project_type.py and slither_project_runner.py don't need to know which
ingestion path was used.

Destination: app/services/github_fetch.py
Runs under: .venv-app

Requires `git` on PATH. On Windows this is typically fine since git is
usually already installed for the repo itself (you're on Git Bash), but
if invoked from a service context confirm git.exe is discoverable the
same way you had to fix solc discoverability for Slither.
"""

import re
import subprocess
from pathlib import Path

from app.services.workspace_manager import WorkspaceError

_GITHUB_URL_RE = re.compile(
    r"^https://github\.com/[\w.-]+/[\w.-]+(?:\.git)?/?$"
)

CLONE_TIMEOUT_SECONDS = 120


def validate_github_url(url: str) -> str:
    """Basic allowlist validation - github.com HTTPS URLs only. Reject
    anything else (git://, ssh://, arbitrary hosts) to avoid becoming an
    open proxy for cloning arbitrary/internal git hosts."""
    if not _GITHUB_URL_RE.match(url.strip()):
        raise WorkspaceError(
            "Only https://github.com/<owner>/<repo> URLs are supported"
        )
    return url.strip().rstrip("/")


def clone_repo(github_url: str, job_dir: Path, ref: str = "main") -> Path:
    """
    Shallow clone into job_dir/source/. Returns the project root path
    (same convention as extract_zip's return value).
    """
    url = validate_github_url(github_url)
    source_dir = job_dir / "source"

    cmd = [
        "git", "clone",
        "--depth", "1",
        "--branch", ref,
        "--single-branch",
        url,
        str(source_dir),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise WorkspaceError(f"git clone timed out after {CLONE_TIMEOUT_SECONDS}s")

    if result.returncode != 0:
        # Common case: ref doesn't exist, repo private/not found, etc.
        raise WorkspaceError(f"git clone failed: {result.stderr.strip()[:500]}")

    # Strip .git so it's not accidentally picked up by later steps
    git_dir = source_dir / ".git"
    if git_dir.exists():
        import shutil
        shutil.rmtree(git_dir, ignore_errors=True)

    return source_dir