"""
Applicability / false-positive verification layer.

Runs AFTER Slither's own detection and BEFORE the LLM explains a finding.
For detector categories where the actual applicability of the flagged risk
can be mechanically checked against the source, this module does that check
and produces structured, deterministic evidence — rather than asking the
LLM to re-derive facts it can get wrong (e.g. claiming a low-level call's
return value is unhandled when a `require()` on the next line already
handles it).

Design note: for reentrancy-eth/reentrancy-no-eth specifically, Slither's
own dataflow analysis is more reliable than anything a regex-based checker
here could re-derive — those detectors only fire because Slither already
confirmed an external call precedes a relevant state write. This module
does NOT attempt to second-guess that; it only extracts the concrete line
numbers/expressions as structured evidence (addresses point #6) and lets
`correlation.py` do the cross-finding linking (point #5).

Where this module DOES add real verification value: `low-level-calls`
(Slither flags every raw .call/.delegatecall/.staticcall regardless of
whether the return value is checked — that's a materially different,
narrower question that IS mechanically checkable) and `tx-origin`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ApplicabilityResult:
    check: str
    facts: dict = field(default_factory=dict)
    facts_block: str = ""  # ready-to-inject prompt text
    likely_false_positive: bool = False
    false_positive_reason: str = ""


# Matches the boolean success variable from a low-level call, e.g.
# "(bool ok, )" or "(bool success, bytes memory data)" or "bool ok ="
_CALL_RESULT_VAR_RE = re.compile(
    r"\(\s*bool\s+(\w+)\s*,|(?<!\w)bool\s+(\w+)\s*=\s*[\w.]+\.(?:call|delegatecall|staticcall)"
)


def _find_call_result_var(source_lines: str) -> str | None:
    for m in _CALL_RESULT_VAR_RE.finditer(source_lines):
        var = m.group(1) or m.group(2)
        if var:
            return var
    return None


def check_low_level_call_return_handling(source_lines: str) -> ApplicabilityResult:
    """
    Determine whether the boolean success value of a flagged low-level call
    is actually checked (require/if/assert) anywhere in the shown source —
    the exact fact that was previously left to the LLM to guess and got
    wrong (claiming "unhandled" when a require() was right there).
    """
    result = ApplicabilityResult(check="low-level-calls")
    var = _find_call_result_var(source_lines)

    if var is None:
        result.facts["return_var"] = None
        result.facts["checked"] = None
        result.facts_block = (
            "Could not identify the call's boolean result variable from "
            "the shown source — do not assert whether it is checked or "
            "not; describe only what is visible."
        )
        return result

    checked_pattern = re.compile(
        rf"\b(require|assert)\s*\(\s*{re.escape(var)}\b|"
        rf"\bif\s*\(\s*!\s*{re.escape(var)}\b|"
        rf"\bif\s*\(\s*{re.escape(var)}\b"
    )
    checked = bool(checked_pattern.search(source_lines))

    result.facts["return_var"] = var
    result.facts["checked"] = checked
    if checked:
        result.facts_block = (
            f"VERIFIED: the call's success value (`{var}`) IS checked "
            f"(a require/if/assert on `{var}` is present in the shown "
            f"source). Do not describe this as unhandled, and do not "
            f"recommend adding a return-value check — it already exists."
        )
    else:
        result.facts_block = (
            f"VERIFIED: the call's success value (`{var}`) is declared but "
            f"NO require/if/assert on `{var}` appears in the shown source "
            f"— the return value is genuinely unchecked."
        )
    return result


_TX_ORIGIN_RE = re.compile(r"\btx\.origin\b")
_AUTH_CONTEXT_RE = re.compile(r"\b(require|if|assert)\s*\([^)]*tx\.origin")


def check_tx_origin_usage(source_lines: str) -> ApplicabilityResult:
    """tx.origin is a real risk specifically when used for authorization
    (require/if against it); logging or event emission uses are lower-risk.
    """
    result = ApplicabilityResult(check="tx-origin")
    used_in_auth = bool(_AUTH_CONTEXT_RE.search(source_lines))
    result.facts["used_in_auth_check"] = used_in_auth
    result.facts_block = (
        "VERIFIED: tx.origin is used inside a require/if/assert condition "
        "— this is a genuine phishing-style authorization-bypass risk."
        if used_in_auth
        else "tx.origin appears in the shown source but NOT inside a "
        "require/if/assert condition — confirm from context whether it's "
        "actually gating access before treating this as a full-severity "
        "auth-bypass finding."
    )
    return result


# Structured evidence extraction (#6): pull concrete facts (line numbers,
# variable names, call/write ordering) directly out of Slither's own
# elements/raw_description rather than trusting free-text LLM narration.

_LINE_REF_RE = re.compile(r"#(\d+)(?:-(\d+))?")


def extract_reentrancy_evidence(raw_description: str) -> dict:
    """
    Slither's reentrancy-* raw_description is highly structured
    (External calls: / State variables written after the call(s):) —
    parse it directly instead of asking the LLM to re-summarize it, so the
    evidence in the final report is guaranteed to match Slither's own
    findings exactly.
    """
    evidence: dict = {"external_calls": [], "state_writes": []}
    section = None
    for line in raw_description.splitlines():
        stripped = line.strip()
        if stripped.startswith("External calls"):
            section = "external_calls"
            continue
        if stripped.startswith("State variables written"):
            section = "state_writes"
            continue
        if stripped.startswith(("Reentrancy in", "It is used by", "can be used in")):
            section = None
            continue
        if section and stripped.startswith("-"):
            m = _LINE_REF_RE.search(stripped)
            line_no = int(m.group(1)) if m else None
            expr = stripped.lstrip("- ").split(" (")[0].strip()
            evidence[section].append({"expr": expr, "line": line_no})

    if evidence["external_calls"] and evidence["state_writes"]:
        call_line = evidence["external_calls"][0]["line"]
        write_line = evidence["state_writes"][0]["line"]
        if call_line is not None and write_line is not None:
            evidence["ordering_confirmed"] = call_line < write_line
            evidence["call_line"] = call_line
            evidence["write_line"] = write_line
    return evidence


def evidence_facts_block(check: str, raw_description: str) -> str:
    """Dispatch to the right evidence extractor and render a prompt-ready
    facts block for the given check, or an empty string if none applies."""
    if check in ("reentrancy-eth", "reentrancy-no-eth"):
        ev = extract_reentrancy_evidence(raw_description)
        if "call_line" in ev:
            return (
                f"EVIDENCE (parsed directly from Slither's own output, not "
                f"inferred): external call at line {ev['call_line']} "
                f"(`{ev['external_calls'][0]['expr']}`); state write at "
                f"line {ev['write_line']} (`{ev['state_writes'][0]['expr']}`); "
                f"call precedes write: {ev['ordering_confirmed']}."
            )
    return ""