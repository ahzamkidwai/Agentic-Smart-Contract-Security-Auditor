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


# Small, extensible mapping from Slither detector names -> SWC Registry IDs.
# Extend this as you add more docs to data/swc_registry/.
SLITHER_CHECK_TO_SWC: dict[str, str] = {
    "reentrancy-eth": "SWC-107",
    "reentrancy-no-eth": "SWC-107",
    "reentrancy-benign": "SWC-107",
    "reentrancy-events": "SWC-107",
    "tx-origin": "SWC-115",
    "unchecked-transfer": "SWC-104",
    "unchecked-lowlevel": "SWC-104",
    "unchecked-send": "SWC-104",
    "arbitrary-send-eth": "SWC-105",
    "suicidal": "SWC-106",
    "integer-overflow": "SWC-101",
    "locked-ether": "SWC-101",
    "timestamp": "SWC-116",
}


def normalize_findings(raw_slither_output: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Flatten slither's --json 'detectors' output into a simple list of dicts:
    {id, check, title, description, severity, confidence, swc_id, elements}
    """
    detectors = raw_slither_output.get("results", {}).get("detectors", [])
    findings = []

    for idx, d in enumerate(detectors):
        check = d.get("check", "unknown")
        findings.append(
            {
                "id": f"finding-{idx}",
                "check": check,
                "title": check.replace("-", " ").title(),
                "description": d.get("description", "").strip(),
                "severity": d.get("impact", "Informational"),
                "confidence": d.get("confidence", "Medium"),
                "swc_id": SLITHER_CHECK_TO_SWC.get(check),
                "elements": [
                    {
                        "type": el.get("type"),
                        "name": el.get("name"),
                        "source_mapping": el.get("source_mapping", {}),
                    }
                    for el in d.get("elements", [])
                ],
            }
        )
    return findings