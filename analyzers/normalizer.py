"""
Turns Slither's and Mythril's very different raw JSON shapes into a
single list[Finding], and provides the folder-level ("production")
runners that walk a whole contracts/ directory.
"""
import os
import re

from schemas import Finding, Severity
from analyzers.slither_runner import run_slither
from analyzers.mythril_runner import run_mythril

# Slither doesn't tag SWC IDs natively - map the common detector names
# ourselves. Extend this as you encounter more detectors in real repos.
SLITHER_TO_SWC = {
    "reentrancy-eth": "SWC-107",
    "reentrancy-no-eth": "SWC-107",
    "reentrancy-benign": "SWC-107",
    "reentrancy-events": "SWC-107",
    "tx-origin": "SWC-115",
    "unchecked-transfer": "SWC-104",
    "unchecked-lowlevel": "SWC-104",
    "integer-overflow": "SWC-101",
    "timestamp": "SWC-116",
    "weak-prng": "SWC-120",
    "suicidal": "SWC-106",
    "arbitrary-send-eth": "SWC-105",
    "delegatecall-loop": "SWC-112",
    "unprotected-upgrade": "SWC-124",
}

SEVERITY_MAP_SLITHER = {
    "High": Severity.high,
    "Medium": Severity.medium,
    "Low": Severity.low,
    "Informational": Severity.informational,
    "Optimization": Severity.optimization,
}

SEVERITY_MAP_MYTHRIL = {
    "High": Severity.high,
    "Medium": Severity.medium,
    "Low": Severity.low,
}


def normalize_slither(raw_findings: list[dict]) -> list[Finding]:
    findings = []
    for item in raw_findings:
        severity = SEVERITY_MAP_SLITHER.get(item.get("impact", "Informational"), Severity.informational)
        elements = item.get("elements", [])
        src_mapping = elements[0]["source_mapping"] if elements else {}
        lines = src_mapping.get("lines", [0]) or [0]
        filename = src_mapping.get("filename_relative", "unknown")
        check_name = item.get("check", "unknown")

        findings.append(Finding(
            source_tool="slither",
            swc_id=SLITHER_TO_SWC.get(check_name),
            title=check_name.replace("-", " ").title(),
            severity=severity,
            contract_file=filename,
            contract_name=elements[0].get("name") if elements else None,
            line_start=min(lines),
            line_end=max(lines),
            raw_description=item.get("description", "")[:1000],
        ))
    return findings


def normalize_mythril(raw_findings: list[dict], contract_file: str) -> list[Finding]:
    findings = []
    for item in raw_findings:
        swc_id = item.get("swc-id") or item.get("swc_id")
        findings.append(Finding(
            source_tool="mythril",
            swc_id=f"SWC-{swc_id}" if swc_id else None,
            title=item.get("title", "unknown"),
            severity=SEVERITY_MAP_MYTHRIL.get(item.get("severity", "Low"), Severity.low),
            contract_file=contract_file,
            contract_name=item.get("contract"),
            line_start=item.get("lineno", 0) or 0,
            line_end=item.get("lineno", 0) or 0,
            raw_description=item.get("description", "")[:1000],
        ))
    return findings


def is_pure_interface(sol_path: str) -> bool:
    """
    True if the file only declares interface(s) with no contract/library
    logic. Neither Slither's vulnerability detectors nor Mythril's symbolic
    execution can find anything in a file with no function bodies, so this
    is used to skip wasted Mythril calls. Abstract contracts (some bodies,
    some not) are intentionally NOT caught by this - they have real logic
    worth analyzing.
    """
    with open(sol_path, encoding="utf-8", errors="ignore") as f:
        content = f.read()
    has_interface = bool(re.search(r"\binterface\s+\w+", content))
    has_contract_or_library = bool(re.search(r"\b(contract|library)\s+\w+", content))
    return has_interface and not has_contract_or_library


def run_slither_project(project_dir: str) -> list[Finding]:
    """One call analyzes the whole project - imports/remappings resolved automatically."""
    raw = run_slither(project_dir)
    return normalize_slither(raw)


def run_mythril_project(project_dir: str) -> list[Finding]:
    """Mythril has no project mode - loop per .sol file, skipping pure interfaces."""
    findings = []
    for root, _, files in os.walk(project_dir):
        for fname in files:
            if not fname.endswith(".sol"):
                continue
            fpath = os.path.join(root, fname)
            if is_pure_interface(fpath):
                continue
            raw = run_mythril(fpath)
            findings.extend(normalize_mythril(raw, fname))
    return findings


def analyze_single_file(contract_path: str, contract_filename: str) -> list[Finding]:
    """Convenience: run both tools against one uploaded file."""
    raw_s = run_slither(contract_path)
    raw_m = run_mythril(contract_path)
    return normalize_slither(raw_s) + normalize_mythril(raw_m, contract_filename)


def analyze_project(project_dir: str) -> list[Finding]:
    """Convenience: run both tools against a whole project directory."""
    return run_slither_project(project_dir) + run_mythril_project(project_dir)
