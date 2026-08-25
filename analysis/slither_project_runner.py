"""
analysis/slither_project_runner.py

Runs under .venv-analysis. Invoked via subprocess.run() from the .venv-app
side (same contract as your existing slither_runner.py: JSON in via argv,
JSON out via stdout). This is the project-wide sibling of that script -
it compiles and analyzes the WHOLE Hardhat/Foundry project in one Slither
invocation (required for correct cross-file/inheritance resolution) and
then groups the raw findings by file before handing back to .venv-app.

Destination: analysis/slither_project_runner.py

Usage (mirrors your existing subprocess.run() call site):
    python analysis/slither_project_runner.py '{"project_root": "C:/Users/.../source", "project_type": "hardhat", "solc_version": "0.8.19"}'

Output: JSON on stdout:
    {
      "success": true,
      "files": {
        "contracts/VulnerableBank.sol": [ <raw slither finding>, ... ],
        "contracts/SafeVault.sol": [ ... ]
      },
      "raw_finding_count": 17
    }
  or on failure:
    {"success": false, "error": "..."}

slither and solc-select are resolved to explicit full paths inside
.venv-analysis (see _resolve_venv_executable() below) rather than relying on
PATH search, since Windows' subprocess executable lookup does not reliably
honor a custom env={"PATH": ...} override for the initial executable name -
this was the actual cause of a WinError 2 here even when PATH looked correct.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

SLITHER_TIMEOUT_SECONDS = 300


def _resolve_venv_executable(name: str, venv_scripts: str) -> str:
    """
    Resolve `name` to an explicit full path inside .venv-analysis's
    Scripts/bin directory, rather than trusting env={"PATH": ...} to make
    a bare command name resolvable. This matters specifically on Windows:
    subprocess.run()'s executable-name search does NOT reliably honor a
    custom PATH passed via env= for the *initial* lookup of the executable
    itself (this is a longstanding CreateProcess quirk, unlike POSIX exec
    family calls) - it can still fail with WinError 2 even though the env
    dict looks correct. Building the full path ourselves sidesteps that
    entirely.
    """
    scripts_dir = Path(venv_scripts)
    if sys.platform == "win32":
        candidates = [scripts_dir / f"{name}.exe", scripts_dir / f"{name}-script.py"]
    else:
        candidates = [scripts_dir / name]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    # Fall back to PATH search (covers non-standard installs / posix where
    # this quirk doesn't apply) before giving up with a clear error.
    import shutil
    found = shutil.which(name)
    if found:
        return found

    raise FileNotFoundError(
        f"Could not locate '{name}' in .venv-analysis ({scripts_dir}) or on PATH. "
        f"Confirm it's installed in .venv-analysis (pip show {name})."
    )


def _slither_command(venv_scripts: str) -> list[str]:
    slither_exe = _resolve_venv_executable("slither", venv_scripts)
    return [slither_exe, ".", "--json", "-"]  # "-" = JSON to stdout


def run_project_slither(project_root: str, project_type: str, solc_version: str | None = None) -> dict:
    root = Path(project_root)
    if not root.exists():
        return {"success": False, "error": f"project_root does not exist: {project_root}"}

    venv_scripts = str(Path(sys.executable).parent)  # .venv-analysis/Scripts (Win) or /bin (posix)

    if solc_version:
        try:
            solc_select_exe = _resolve_venv_executable("solc-select", venv_scripts)
        except FileNotFoundError as e:
            return {"success": False, "error": str(e)}

        select_result = subprocess.run(
            [solc_select_exe, "use", solc_version],
            capture_output=True, text=True, timeout=60,
        )
        if select_result.returncode != 0:
            return {
                "success": False,
                "error": f"solc-select failed for {solc_version}: {select_result.stderr.strip()}",
            }

    try:
        cmd = _slither_command(venv_scripts)
    except FileNotFoundError as e:
        return {"success": False, "error": str(e)}

    try:
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=SLITHER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"slither timed out after {SLITHER_TIMEOUT_SECONDS}s"}

    # Slither exits non-zero when findings exist - only treat truly empty
    # stdout as a hard failure (compile error, no output at all).
    if not result.stdout.strip():
        return {
            "success": False,
            "error": f"slither produced no output. stderr: {result.stderr.strip()[:2000]}",
        }

    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"failed to parse slither JSON: {e}"}

    if not raw.get("success", True) and raw.get("error"):
        return {"success": False, "error": raw["error"]}

    detectors = raw.get("results", {}).get("detectors", [])
    grouped = _group_by_file(detectors)

    return {
        "success": True,
        "files": grouped,
        "raw_finding_count": len(detectors),
    }


def _group_by_file(detectors: list[dict]) -> dict[str, list[dict]]:
    """
    Each Slither finding can touch multiple files via `elements[]`
    (e.g. a cross-contract call). We file it under the *primary* element's
    file - conventionally elements[0] - and let correlation.py (on the
    .venv-app side) handle cross-referencing secondary files rather than
    duplicating the full finding into every touched file's bucket.
    """
    grouped: dict[str, list[dict]] = {}
    for finding in detectors:
        elements = finding.get("elements", [])
        if not elements:
            grouped.setdefault("_unattributed", []).append(finding)
            continue
        primary_file = (
            elements[0]
            .get("source_mapping", {})
            .get("filename_relative", "_unattributed")
        )
        grouped.setdefault(primary_file, []).append(finding)
    return grouped


if __name__ == "__main__":
    # Two ways to supply the payload:
    #   1. As argv[1] - what routes_project.py's subprocess.run() uses,
    #      unchanged, since Python's own subprocess module passes args as
    #      a real argv array with no shell re-parsing involved.
    #   2. Via stdin - for manual CLI testing on Windows/PowerShell, where
    #      embedding a JSON string with escaped quotes as a literal argv
    #      token is unreliable (PowerShell's own quoting rules collide with
    #      how Windows' CommandLineToArgvW reconstructs argv for the child
    #      process). Piping avoids that entirely: no re-parsing, just bytes.
    if len(sys.argv) > 1:
        payload = json.loads(sys.argv[1])
    else:
        payload = json.loads(sys.stdin.read())

    output = run_project_slither(
        project_root=payload["project_root"],
        project_type=payload["project_type"],
        solc_version=payload.get("solc_version"),
    )
    print(json.dumps(output))