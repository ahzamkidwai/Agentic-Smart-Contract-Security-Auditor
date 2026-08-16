"""
Severity reassessment.

Slither's own `impact` rating (Critical/High/Medium/Low/Informational/
Optimization) is a reasonable default but doesn't account for
finding-specific exploitability signals this pipeline already computes
elsewhere: whether a return value is actually checked (applicability.py),
whether a compiler bug is actually triggerable (compiler_analysis.py),
whether a reentrancy guard is already present (contract_header), etc.

This is a deterministic RULE ENGINE, not an LLM guess — every adjustment
below is traceable to a concrete, checkable fact. It only ever produces a
severity that is defensible from those facts; it does not invent
exploit-likelihood judgments the underlying data can't support.
"""
from __future__ import annotations

from dataclasses import dataclass

_SEVERITY_ORDER = ["Optimization", "Informational", "Low", "Medium", "High", "Critical"]


def _clamp(sev: str) -> str:
    return sev if sev in _SEVERITY_ORDER else "Informational"


def _shift(sev: str, steps: int) -> str:
    idx = _SEVERITY_ORDER.index(_clamp(sev))
    idx = max(0, min(len(_SEVERITY_ORDER) - 1, idx + steps))
    return _SEVERITY_ORDER[idx]


@dataclass
class SeverityAssessment:
    original: str
    adjusted: str
    rationale: str


_REENTRANCY_GUARD_RE = None  # set lazily to avoid import cost if unused


def reassess_severity(
    check: str,
    base_severity: str,
    *,
    contract_header: str = "",
    applicability_facts: dict | None = None,
    compiler_all_not_applicable: bool = False,
) -> SeverityAssessment:
    facts = applicability_facts or {}
    base_severity = _clamp(base_severity)

    if check in ("reentrancy-eth", "reentrancy-no-eth"):
        import re

        guarded = bool(
            re.search(r"\bnonReentrant\b|\bReentrancyGuard\b", contract_header)
        )
        if guarded:
            return SeverityAssessment(
                original=base_severity,
                adjusted=_shift(base_severity, -3),
                rationale=(
                    "Contract header shows a ReentrancyGuard/nonReentrant "
                    "modifier — downgraded from the detector default since "
                    "the primary exploit path is already mitigated. "
                    "Verify the modifier is actually applied to this "
                    "specific function before treating this as fully "
                    "resolved."
                ),
            )
        if check == "reentrancy-eth":
            return SeverityAssessment(
                original=base_severity,
                adjusted="Critical" if base_severity == "High" else base_severity,
                rationale=(
                    "Unguarded ETH-sending reentrancy with confirmed "
                    "call-before-write ordering (see evidence) and no "
                    "ReentrancyGuard present — funds are directly at risk, "
                    "escalated from the detector's default High."
                ),
            )
        return SeverityAssessment(base_severity, base_severity, "No adjustment.")

    if check == "low-level-calls":
        # This detector is purely informational about the call mechanism —
        # never escalate it regardless of what's visible in surrounding
        # code; ordering/reentrancy risk belongs to a different, separately
        # reported finding (see correlation.py). Whether the return value
        # is checked doesn't change this detector's own severity either —
        # that's unchecked-lowlevel's concern if it fires separately.
        return SeverityAssessment(
            original=base_severity,
            adjusted="Informational",
            rationale=(
                "low-level-calls is purely informational about call "
                "mechanism; it never escalates on its own. If the return "
                "value is genuinely unchecked, that's `unchecked-lowlevel` "
                "and/or SWC-104's concern, not this finding's."
            ),
        )

    if check == "unchecked-lowlevel":
        checked = facts.get("checked")
        if checked is True:
            return SeverityAssessment(
                original=base_severity,
                adjusted="Informational",
                rationale=(
                    "Verified: the return value IS checked in the shown "
                    "source (see applicability evidence) — downgraded, "
                    "this detector's actual concern doesn't apply here."
                ),
            )
        return SeverityAssessment(base_severity, base_severity, "No adjustment.")

    if check == "solc-version":
        if compiler_all_not_applicable:
            return SeverityAssessment(
                original=base_severity,
                adjusted="Informational",
                rationale=(
                    "Every named compiler bug is confirmed not triggerable "
                    "by this source (see compiler-bug applicability "
                    "analysis) — no practical impact, kept at "
                    "Informational rather than implying an active issue."
                ),
            )
        return SeverityAssessment(base_severity, base_severity, "No adjustment.")

    if check == "tx-origin":
        if facts.get("used_in_auth_check") is False:
            return SeverityAssessment(
                original=base_severity,
                adjusted=_shift(base_severity, -1),
                rationale=(
                    "tx.origin is not used inside a require/if/assert in "
                    "the shown source — likely not gating access, "
                    "downgraded pending manual confirmation of actual use."
                ),
            )
        return SeverityAssessment(base_severity, base_severity, "No adjustment.")

    if check == "immutable-states":
        return SeverityAssessment(
            original=base_severity,
            adjusted="Informational" if base_severity == "Optimization" else base_severity,
            rationale="Gas optimization, not a security issue — never a real severity finding.",
        )

    return SeverityAssessment(base_severity, base_severity, "No specific rule for this check.")