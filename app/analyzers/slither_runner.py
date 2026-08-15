"""
Subprocess wrapper around the `slither` CLI living in .venv-analysis.

We NEVER `import slither` here. This module runs inside .venv-app and does
not have slither's dependency tree installed at all -- we only invoke the
binary and parse its JSON output over a tempfile. This subprocess boundary
is what makes the two-venv split possible: it doesn't matter that the two
environments have mutually incompatible eth-account/web3 pins, because
they never share a Python import space.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from config import settings


class SlitherExecutionError(RuntimeError):
    pass


def run_slither(target_path: str) -> dict[str, Any]:
    """
    Run slither against a contract file or a full repo directory and return
    the parsed --json output.

    target_path: path to a .sol file, or a directory containing a Solidity
    project (Foundry/Hardhat root, or a plain folder of .sol files).
    """
    target = Path(target_path)
    if not target.exists():
        raise FileNotFoundError(f"Target not found: {target_path}")

    slither_exe = settings.SLITHER_BIN
    # .venv-analysis/Scripts (Windows) or .venv-analysis/bin (Linux/Mac) --
    # this is where solc-select's `solc` shim also lives. Slither spawns
    # `solc` as a bare command name internally, so it needs this directory
    # on PATH for that nested subprocess call to resolve it.
    venv_scripts_dir = str(Path(slither_exe).parent)

    env = os.environ.copy()
    env["PATH"] = venv_scripts_dir + os.pathsep + env.get("PATH", "")

    # NOTE: we only want a unique *path*, not an actual file on disk --
    # Slither's --json refuses to overwrite a file that already exists,
    # and NamedTemporaryFile() creates the file the moment it's called.
    json_out_path = os.path.join(
        tempfile.gettempdir(), f"slither_{next(tempfile._get_candidate_names())}.json"
    )

    cmd = [slither_exe, str(target), "--json", json_out_path]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=settings.SLITHER_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as e:
        raise SlitherExecutionError(
            f"Could not find slither binary at {slither_exe}. "
            "Did you create .venv-analysis and run `pip install -r "
            "requirements-analysis.txt` inside it? See scripts/setup_venvs.sh."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise SlitherExecutionError(
            f"slither timed out after {settings.SLITHER_TIMEOUT_SECONDS}s"
        ) from e

    # Slither exits non-zero whenever findings exist -- that's expected and
    # NOT an error. We only treat it as a real failure if no JSON came out.
    json_path = Path(json_out_path)
    if not json_path.exists() or json_path.stat().st_size == 0:
        raise SlitherExecutionError(
            f"slither produced no output.\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )

    with open(json_path, "r") as f:
        raw = json.load(f)

    json_path.unlink(missing_ok=True)

    if not raw.get("success", False) and not raw.get("results"):
        raise SlitherExecutionError(
            f"slither reported failure: {raw.get('error', 'unknown error')}"
        )

    return raw


# ---------------------------------------------------------------------------
# Slither detector name → SWC Registry ID mapping.
#
# Rules for this table:
#   1. Only map a detector to an SWC ID when the relationship is
#      unambiguous and well-documented in the SWC Registry.  A missing entry
#      (None) is ALWAYS better than a wrong one — the LLM prompt handles the
#      "no SWC" case gracefully.
#   2. "locked-ether" is intentionally absent from SWC-101 (Integer
#      Overflow). The correct class is SWC-132 (Unexpected Ether Balance),
#      which we track as None until we add its registry doc.
#   3. "integer-overflow" is not emitted by modern Slither on >=0.8
#      contracts (the compiler catches it). It still appears in legacy
#      codebases, so we keep the mapping to SWC-101.
# ---------------------------------------------------------------------------
SLITHER_CHECK_TO_SWC: dict[str, str] = {
    # --- Reentrancy (SWC-107) ---
    "reentrancy-eth": "SWC-107",
    "reentrancy-no-eth": "SWC-107",
    "reentrancy-benign": "SWC-107",
    "reentrancy-events": "SWC-107",
    "reentrancy-unlimited-gas": "SWC-107",

    # --- tx.origin (SWC-115) ---
    "tx-origin": "SWC-115",

    # --- Unchecked return values (SWC-104) ---
    "unchecked-transfer": "SWC-104",
    "unchecked-lowlevel": "SWC-104",
    "unchecked-send": "SWC-104",

    # --- Unprotected ETH send (SWC-105) ---
    "arbitrary-send-eth": "SWC-105",
    "arbitrary-send-erc20": "SWC-105",

    # --- Self-destruct (SWC-106) ---
    "suicidal": "SWC-106",
    "controlled-destroy": "SWC-106",

    # --- Integer arithmetic (SWC-101) ---
    # Only fired by Slither on pre-0.8 contracts; Solidity >=0.8 reverts natively.
    "integer-overflow": "SWC-101",
    "tautology": "SWC-101",

    # --- Timestamp dependence (SWC-116) ---
    "timestamp": "SWC-116",

    # --- Delegatecall to untrusted callee (SWC-112) ---
    "controlled-delegatecall": "SWC-112",
    "delegatecall-loop": "SWC-112",

    # --- DoS with failed call (SWC-113) ---
    "msg-value-loop": "SWC-113",
    "calls-loop": "SWC-113",

    # --- Weak randomness (SWC-120) ---
    "weak-prng": "SWC-120",

    # --- Shadowing state variables (SWC-119) ---
    "shadowing-state": "SWC-119",
    "shadowing-abstract": "SWC-119",
    "shadowing-local": "SWC-119",
    "shadowing-builtin": "SWC-119",

    # --- Uninitialized state / local variables (SWC-109) ---
    "uninitialized-state": "SWC-109",
    "uninitialized-local": "SWC-109",
    "uninitialized-storage": "SWC-109",

    # --- Locked ether → SWC-132 (Unexpected Ether Balance) ---
    # Previously this was incorrectly mapped to SWC-101 (Integer Overflow).
    "locked-ether": "SWC-132",

    # --- Access control / missing modifiers (informational, no clean SWC) ---
    "missing-zero-check": None,
    "events-maths": None,
    "events-access": None,
    "low-level-calls": None,
    "assembly": None,
    "dead-code": None,
    "reentrancy-read-before-write": "SWC-107",
}


def _extract_source_lines(element: dict, target_path: str) -> str:
    """
    Extract the flagged source lines from a Slither element's source_mapping.

    Returns a compact string like:
        "Line 42-45 of MyContract.sol:\n    balances[msg.sender] -= amount;\n    (bool ok,) = ..."
    or an empty string if the mapping is unavailable / the file can't be read.
    """
    sm = element.get("source_mapping", {})
    filename = sm.get("filename_relative") or sm.get("filename_absolute", "")
    lines_obj = sm.get("lines")

    if not filename or not lines_obj:
        return ""

    try:
        # Prefer the relative path resolved from the target location.
        candidate = Path(target_path)
        if candidate.is_file():
            base = candidate.parent
        else:
            base = candidate
        filepath = base / filename
        if not filepath.exists():
            filepath = Path(filename)  # try as absolute / cwd-relative
        if not filepath.exists():
            return ""

        all_lines = filepath.read_text(encoding="utf-8", errors="replace").splitlines()
        # lines_obj is a list of 1-based line numbers
        start = min(lines_obj) - 1  # convert to 0-based
        end = max(lines_obj)        # slicing end is exclusive
        snippet = "\n".join(all_lines[start:end])
        line_range = (
            f"Line {min(lines_obj)}"
            if min(lines_obj) == max(lines_obj)
            else f"Lines {min(lines_obj)}-{max(lines_obj)}"
        )
        return f"{line_range} of {filename}:\n{snippet}"
    except Exception:
        return ""


def normalize_findings(
    raw_slither_output: dict[str, Any],
    target_path: str = "",
) -> list[dict[str, Any]]:
    """
    Flatten slither's --json 'detectors' output into a simple list of dicts:
    {id, check, title, description, severity, confidence, swc_id, elements,
     source_lines}

    source_lines: the actual Solidity lines Slither flagged, extracted from
    source_mapping.  Empty string when unavailable.
    """
    detectors = raw_slither_output.get("results", {}).get("detectors", [])
    findings = []

    for idx, d in enumerate(detectors):
        check = d.get("check", "unknown")

        elements = [
            {
                "type": el.get("type"),
                "name": el.get("name"),
                "source_mapping": el.get("source_mapping", {}),
            }
            for el in d.get("elements", [])
        ]

        # Collect the flagged source lines from every element in the finding.
        # De-duplicate while preserving order.
        seen: set[str] = set()
        snippet_parts: list[str] = []
        for el in d.get("elements", []):
            snippet = _extract_source_lines(el, target_path)
            if snippet and snippet not in seen:
                seen.add(snippet)
                snippet_parts.append(snippet)
        source_lines = "\n\n".join(snippet_parts)

        # Use Slither's own "description" as the human-readable title where
        # possible; fall back to a cleaned-up version of the check name.
        slither_title = d.get("description", "").strip().splitlines()[0] if d.get("description") else ""
        title = slither_title if slither_title else check.replace("-", " ").title()

        findings.append(
            {
                "id": f"finding-{idx}",
                "check": check,
                "title": title,
                "description": d.get("description", "").strip(),
                "severity": d.get("impact", "Informational"),
                "confidence": d.get("confidence", "Medium"),
                "swc_id": SLITHER_CHECK_TO_SWC.get(check, None),
                "elements": elements,
                "source_lines": source_lines,
            }
        )
    return findings