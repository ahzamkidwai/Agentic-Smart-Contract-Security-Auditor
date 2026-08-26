"""
app/services/source_reading.py

Small shared helpers for reading Solidity source off disk, used by both the
single-file/pasted-code pipeline (app/api/routes.py) and the project-wide
pipeline (app/api/routes_project.py). Split out so both call sites stay in
sync instead of drifting copies.
"""
from __future__ import annotations

from pathlib import Path

_MAX_FULL_SOURCE_BYTES = 300_000  # sanity cap; typical contracts are a few KB


def read_contract_header(target_path: str | Path, max_lines: int = 30) -> str:
    """
    Read the first ``max_lines`` lines of a Solidity file.

    Returns an empty string if target_path is a directory or cannot be read.
    This gives the LLM visibility into pragma, imports, and contract-level
    declarations so it can detect already-applied fixes (e.g. ReentrancyGuard
    already imported) without falsely recommending them again.
    """
    p = Path(target_path)
    if not p.is_file() or p.suffix.lower() != ".sol":
        return ""
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[:max_lines])
    except Exception:
        return ""


def read_full_source(target_path: str | Path) -> str:
    """
    Read the entire contract file for deterministic, programmatic checks
    (e.g. compiler_analysis's regex trigger-condition matching) — this is
    NOT injected into the LLM prompt wholesale, only used in Python logic,
    so there's no prompt-size concern; only a sanity cap against
    pathological input.
    """
    p = Path(target_path)
    if not p.is_file() or p.suffix.lower() != ".sol":
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        return text[:_MAX_FULL_SOURCE_BYTES]
    except Exception:
        return ""


def primary_location(elements: list[dict]) -> dict:
    """
    Extract a display-friendly {file, start_line, end_line, lines} location
    from a raw Slither finding's `elements` list (as produced by
    normalize_findings / the RawFinding.elements field).

    Picks the first element that actually carries a source_mapping with line
    info, since that's consistently "the" primary flagged location for a
    finding (mirrors slither_project_runner._group_by_file's convention of
    using elements[0] as the primary file).
    """
    for el in elements:
        sm = (el or {}).get("source_mapping") or {}
        lines = sm.get("lines") or []
        filename = sm.get("filename_relative") or sm.get("filename_short") or sm.get("filename_absolute")
        if filename and lines:
            return {
                "file": filename,
                "start_line": min(lines),
                "end_line": max(lines),
                "lines": sorted(lines),
            }
    return {"file": None, "start_line": None, "end_line": None, "lines": []}