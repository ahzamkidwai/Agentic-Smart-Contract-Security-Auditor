"""
app/services/project_type.py

Detects whether an extracted/cloned project is Hardhat or Foundry, and runs
the corresponding dependency install with safety guardrails:
  - npm install --ignore-scripts   (no postinstall/preinstall code execution)
  - forge install (git submodule based, lower script-execution risk already)
  - hard timeout on the install step
  - install failures surface as a clean AuditJob.FAILED status + error
    message rather than silently degrading to "0 findings"

Destination: app/services/project_type.py
Runs under: .venv-app

This does NOT invoke solc/Slither itself - that stays in .venv-analysis
(see analysis/slither_project_runner.py). This module only prepares the
project directory so that side's `slither .` / `slither --hardhat` /
`slither --foundry` invocation has what it needs (node_modules or lib/).

MONOREPO SUPPORT: the Hardhat/Foundry config often isn't at the repo root
(e.g. a pnpm workspace with the actual contracts under contracts/ or
packages/contracts/). find_project_root() locates it without doing a naive
full-tree walk: it tries the root first, then a handful of conventional
subfolder names as a fast path, and only falls back to a directory walk
that is BOTH pruned (skips node_modules/.git/dist/build/etc. entirely -
the walker never descends into them) and depth-bounded, so it can't blow
up scan time on a large monorepo with many unrelated packages.
"""

import os
import shutil
import subprocess
import sys
from enum import Enum
from pathlib import Path

from app.services.workspace_manager import WorkspaceError

INSTALL_TIMEOUT_SECONDS = 300

# Directories that can never contain a project's own hardhat.config/foundry.toml
# in a way we care about - pruning these means the walker skips them entirely
# rather than descending in and wasting time (or matching vendored configs).
_PRUNE_DIRS = {
    "node_modules", ".git", "dist", "build", "out", "cache", "artifacts",
    ".next", ".turbo", "coverage", "target", ".venv-analysis", ".venv-app",
}

# Fast-path candidate names, checked before falling back to a walk. Ordered
# by how commonly they're used for "the contracts live here" in practice.
_CONVENTIONAL_SUBDIRS = (
    "contracts",
    "packages/contracts",
    "packages/hardhat",
    "packages/foundry",
    "smart-contracts",
    "packages/smart-contracts",
)

MAX_SEARCH_DEPTH = 4  # levels below repo root the fallback walk will descend


class ProjectType(str, Enum):
    HARDHAT = "hardhat"
    FOUNDRY = "foundry"
    UNKNOWN = "unknown"


def detect_project_type(project_root: Path) -> ProjectType:
    """Checks a SINGLE directory (non-recursive). For monorepo-aware
    detection that searches subdirectories, use find_project_root()."""
    has_hardhat_cfg = any(
        (project_root / name).exists()
        for name in ("hardhat.config.js", "hardhat.config.ts")
    )
    has_foundry_cfg = (project_root / "foundry.toml").exists()

    if has_hardhat_cfg and has_foundry_cfg:
        # Hybrid repos exist; prefer Foundry since forge install is lower-risk
        # and Slither's --foundry mode is generally more predictable.
        return ProjectType.FOUNDRY
    if has_hardhat_cfg:
        return ProjectType.HARDHAT
    if has_foundry_cfg:
        return ProjectType.FOUNDRY
    return ProjectType.UNKNOWN


def find_project_root(repo_root: Path, max_depth: int = MAX_SEARCH_DEPTH) -> tuple[ProjectType, Path]:
    """
    Locates the actual Hardhat/Foundry project root within a (possibly
    monorepo) checkout. Returns (ProjectType.UNKNOWN, repo_root) if nothing
    is found anywhere within max_depth.

    Search order (cheapest first):
      1. repo_root itself
      2. conventional subfolder names (contracts/, packages/contracts/, ...)
      3. pruned, depth-bounded directory walk as a general fallback
    """
    # 1. Repo root
    t = detect_project_type(repo_root)
    if t != ProjectType.UNKNOWN:
        return t, repo_root

    # 2. Conventional subfolder fast path
    for name in _CONVENTIONAL_SUBDIRS:
        candidate = repo_root / name
        if candidate.is_dir():
            t = detect_project_type(candidate)
            if t != ProjectType.UNKNOWN:
                return t, candidate

    # 3. Pruned, depth-bounded fallback walk. dirnames[:] mutation is what
    # makes the pruning actually skip traversal (not just filter results) -
    # os.walk respects in-place edits to the dirnames list it yields.
    root_depth = len(repo_root.resolve().parts)
    for dirpath, dirnames, _filenames in os.walk(repo_root):
        dirnames[:] = [
            d for d in dirnames
            if d not in _PRUNE_DIRS and not d.startswith(".")
        ]

        current = Path(dirpath)
        depth = len(current.resolve().parts) - root_depth
        if depth > max_depth:
            dirnames[:] = []  # stop descending past this point
            continue

        t = detect_project_type(current)
        if t != ProjectType.UNKNOWN:
            return t, current

    return ProjectType.UNKNOWN, repo_root


def _resolve_executable(name: str) -> str:
    """
    Resolve `name` (e.g. "forge", "npm") to a full path rather than trusting
    the inherited PATH. subprocess.run() only sees the PATH of the process
    that spawned Python (uvicorn, itself launched from whatever shell you
    ran `uvicorn.exe main:app` in) - if forge/npm were installed and added
    to PATH in a *different* shell's profile (e.g. Git Bash's .bashrc, while
    uvicorn is running under PowerShell), shutil.which() here will correctly
    fail to find it too, just like PowerShell would. In that case we also
    check a couple of common Windows install locations before giving up.
    """
    found = shutil.which(name)
    if found:
        return found

    if sys.platform == "win32":
        home = Path.home()
        fallback_candidates = {
            "forge": [home / ".foundry" / "bin" / "forge.exe"],
            "npm": [
                Path(os.environ.get("APPDATA", "")) / "npm" / "npm.cmd",
                Path("C:/Program Files/nodejs/npm.cmd"),
            ],
        }
        for candidate in fallback_candidates.get(name, []):
            if candidate.exists():
                return str(candidate)

    raise WorkspaceError(
        f"'{name}' was not found on PATH for the process running the "
        f"FastAPI server. It may be installed and working in your terminal "
        f"(e.g. Git Bash) but not visible to whatever shell you launched "
        f"`uvicorn` from (e.g. PowerShell), since each shell can have its "
        f"own PATH configuration. Either add {name} to that shell's PATH "
        f"permanently (System Properties > Environment Variables, so it "
        f"applies process-wide) and restart the terminal + uvicorn, or "
        f"launch uvicorn from the same Git Bash session where `{name} "
        f"--version` already works."
    )


def install_dependencies(project_root: Path, project_type: ProjectType) -> None:
    """
    Raises WorkspaceError on failure or timeout. Caller should set
    AuditJob.status = FAILED and store the error_message verbatim (truncated)
    so the user knows it's a dependency/build problem, not a "no bugs found".
    """
    if project_type == ProjectType.HARDHAT:
        exe = _resolve_executable("npm")
        cmd = [exe, "install", "--ignore-scripts", "--no-audit", "--no-fund"]
    elif project_type == ProjectType.FOUNDRY:
        exe = _resolve_executable("forge")
        cmd = [exe, "install"]
    else:
        raise WorkspaceError(
            "Could not detect a Hardhat (hardhat.config.js/ts) or "
            "Foundry (foundry.toml) project at the repo root."
        )

    try:
        result = subprocess.run(
            cmd,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=INSTALL_TIMEOUT_SECONDS,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        raise WorkspaceError(
            f"{cmd[0]} install timed out after {INSTALL_TIMEOUT_SECONDS}s"
        )
    except OSError as e:
        # Belt-and-suspenders: _resolve_executable already validated the
        # path exists, but keep this in case of permissions or similar.
        raise WorkspaceError(f"Failed to launch {cmd[0]}: {e}")

    if result.returncode != 0:
        raise WorkspaceError(
            f"Dependency install failed ({cmd[0]}):\n{result.stderr.strip()[:1000]}"
        )


def find_solidity_files(project_root: Path) -> list[Path]:
    """
    Used for pre-flight checks (file count guard) and for the per-file
    grouping fallback if a finding's source_mapping is ever ambiguous.
    Excludes node_modules/lib/dependency dirs so counts reflect the user's
    own contracts, not vendored code.
    """
    excluded_dirs = {"node_modules", "lib", ".git", "artifacts", "cache", "out"}
    results = []
    for path in project_root.rglob("*.sol"):
        if any(part in excluded_dirs for part in path.parts):
            continue
        results.append(path)
    return results