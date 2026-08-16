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
import re
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


# ---------------------------------------------------------------------------
# Source-context extraction.
#
# Earlier versions extracted *only* the exact lines Slither flagged for each
# element. That starved the LLM of context: e.g. a "low-level-calls" element
# flagging just the call-site line wouldn't include the require() checking
# its return value one line below, and the model would confidently (and
# wrongly) claim the return value was unhandled.
#
# This version instead resolves each flagged line to its *enclosing
# function/modifier/constructor body* (best-effort brace-depth scan — not a
# full Solidity parser, but sufficient for realistic single-file contracts),
# marks the specific flagged line(s) with a ">>>" prefix inside that block,
# and — critically — groups all of a finding's elements by their resolved
# block *before* rendering, so a finding with multiple elements landing in
# the same function (e.g. reentrancy-eth's call-site + state-write elements)
# produces ONE annotated block with both lines marked, not two near-duplicate
# copies of the same function with different lines starred.
# ---------------------------------------------------------------------------

_FUNC_SIG_RE = re.compile(
    r"^\s*(function\s+\w+|constructor|modifier\s+\w+|fallback\s*\(|receive\s*\()"
)


def _strip_line_for_brace_count(line: str) -> str:
    """
    Blank out string-literal contents (best-effort, backslash-escape aware)
    and trailing `//` comments so braces inside them don't throw off depth
    counting. Not a full lexer, but adequate for typical Solidity source.
    """
    out: list[str] = []
    in_str: str | None = None
    i = 0
    while i < len(line):
        ch = line[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ("'", '"'):
            in_str = ch
            i += 1
            continue
        if line[i : i + 2] == "//":
            break
        out.append(ch)
        i += 1
    return "".join(out)


def _find_enclosing_block(
    all_lines: list[str],
    flagged_start_0based: int,
    flagged_end_0based: int,
    max_backward: int = 80,
    max_forward: int = 400,
) -> tuple[int, int] | None:
    """
    Locate the function/modifier/constructor body containing the flagged
    line range. Returns (block_start_0based, block_end_0based) inclusive,
    or None if no enclosing signature could be confidently located within
    the search window.
    """
    sig_start = None
    lo = max(0, flagged_start_0based - max_backward)
    for i in range(flagged_start_0based, lo - 1, -1):
        if _FUNC_SIG_RE.match(all_lines[i]):
            sig_start = i
            break
    if sig_start is None:
        return None

    depth = 0
    body_opened = False
    hi = min(len(all_lines), flagged_end_0based + max_forward)
    for i in range(sig_start, hi):
        stripped = _strip_line_for_brace_count(all_lines[i])
        depth += stripped.count("{")
        depth -= stripped.count("}")
        if "{" in stripped:
            body_opened = True
        if body_opened and depth <= 0:
            return (sig_start, i)
    return None


# Cap on the fallback (no enclosing function found) context window, so a
# finding whose flagged lines already span a wide range doesn't balloon
# into dumping most of the file into the prompt.
_FALLBACK_CONTEXT_LINES = 3
_FALLBACK_MAX_SPAN = 40


def _collect_flagged_lines_by_file(
    elements: list[dict], target_path: str
) -> dict[Path, set[int]]:
    """Union every element's flagged (1-based) line numbers, grouped by file."""
    by_file: dict[Path, set[int]] = {}
    for el in elements:
        sm = el.get("source_mapping", {})
        filename = sm.get("filename_relative") or sm.get("filename_absolute", "")
        lines_obj = sm.get("lines")
        if not filename or not lines_obj:
            continue
        filepath = _resolve_filepath(filename, target_path)
        if filepath is None:
            continue
        by_file.setdefault(filepath, set()).update(lines_obj)
    return by_file


def _resolve_filepath(filename: str, target_path: str) -> Path | None:
    candidate = Path(target_path)
    base = candidate.parent if candidate.is_file() else candidate
    filepath = base / filename
    if not filepath.exists():
        filepath = Path(filename)  # try as absolute / cwd-relative
    return filepath if filepath.exists() else None


def _render_annotated_blocks(filepath: Path, flagged_lines_1based: set[int]) -> str:
    """
    Group the given (1-based) flagged line numbers by their resolved
    enclosing block, then render one ">>>"-annotated snippet per distinct
    block (deduplicated — multiple flagged lines in the same function
    collapse into a single block with multiple markers).
    """
    try:
        all_lines = filepath.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""

    # block_bounds -> set of 0-based line indices to mark within it
    blocks: dict[tuple[int, int], set[int]] = {}
    for ln in sorted(flagged_lines_1based):
        idx = ln - 1
        if not (0 <= idx < len(all_lines)):
            continue
        bounds = _find_enclosing_block(all_lines, idx, idx)
        if bounds is None:
            start = max(0, idx - _FALLBACK_CONTEXT_LINES)
            end = min(len(all_lines) - 1, idx + _FALLBACK_CONTEXT_LINES)
            if end - start > _FALLBACK_MAX_SPAN:
                end = start + _FALLBACK_MAX_SPAN
            bounds = (start, end)
        blocks.setdefault(bounds, set()).add(idx)

    rendered: list[str] = []
    for (b_start, b_end), marked in sorted(blocks.items()):
        marked_1based = sorted(i + 1 for i in marked)
        line_desc = (
            f"line {marked_1based[0]}"
            if len(marked_1based) == 1
            else f"lines {', '.join(str(n) for n in marked_1based)}"
        )
        out = [f"Flagged {line_desc} (marked >>>) of {filepath.name}:"]
        for i in range(b_start, b_end + 1):
            prefix = ">>> " if i in marked else "    "
            out.append(f"{prefix}{all_lines[i]}")
        rendered.append("\n".join(out))

    return "\n\n".join(rendered)


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

        # Collect every element's flagged lines, grouped by file, then
        # render one annotated block per distinct enclosing function —
        # this is what prevents (a) losing surrounding context like an
        # adjacent require() check, and (b) the same function being
        # rendered multiple times with different lines starred when a
        # finding (e.g. reentrancy-eth) has several elements landing in it.
        by_file = _collect_flagged_lines_by_file(d.get("elements", []), target_path)
        source_lines = "\n\n".join(
            block
            for filepath, lines in by_file.items()
            if (block := _render_annotated_blocks(filepath, lines))
        )

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