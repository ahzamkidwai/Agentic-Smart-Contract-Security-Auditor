"""
Deterministic compiler-bug ("solc-version") analysis.

Slither's `solc-version` detector fires purely on version-range matching
against its bundled copy of Solidity's official known-bugs list — it does
NOT check whether the bug's actual trigger condition is present in the
source. This produces false positives: e.g. VerbatimInvalidDeduplication
(SOL-2023-3) only affects pure-Yul `verbatim` blocks under the optimizer;
per Solidity's own advisory, "compilation of Solidity sources is not
affected" if the contract never uses `verbatim`. A plain Solidity contract
matching the affected version range is still reported, and an LLM asked to
explain it has no way to know the bug doesn't actually apply — this is
exactly what produced contradictory "fix already applied" / "you must
upgrade" text in earlier runs.

This module resolves each reported bug against the bundled authoritative
data (`data/solidity_known_bugs.json`, sourced from
https://github.com/ethereum/solidity/blob/develop/docs/bugs.json) and
checks known trigger conditions against the actual contract source where
mechanically checkable, producing ground-truth facts for the LLM prompt
instead of leaving it to guess.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_BUGS_PATH = Path(__file__).parent.parent / "knowledge_base" / "data" / "solidity_known_bugs.json"


def _load_bugs() -> dict[str, dict]:
    try:
        raw = json.loads(_BUGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {b["name"]: b for b in raw}


_BUGS_BY_NAME = _load_bugs()

# Bugs whose real trigger condition is described only in prose (not in a
# machine-checkable `check`/`conditions` field) get a curated, verified
# regex here. Verified against Solidity's official bug advisories — do not
# add entries without confirming the actual scope first.
_MANUAL_TRIGGER_REGEX: dict[str, str] = {
    # "Since verbatim is only available in Yul, compilation of Solidity
    # sources is not affected." — https://soliditylang.org/blog/2023/11/08/
    "VerbatimInvalidDeduplication": r"\bverbatim\b",
}


@dataclass
class BugApplicability:
    name: str
    known: bool = False
    introduced: str | None = None
    fixed: str | None = None
    severity: str | None = None
    link: str | None = None
    summary: str | None = None
    applicable: bool | None = None  # True/False if determinable, else None
    applicability_reason: str = ""


@dataclass
class CompilerVersionAnalysis:
    bugs: list[BugApplicability] = field(default_factory=list)
    recommended_min_version: str | None = None
    any_confirmed_applicable: bool = False
    all_confirmed_not_applicable: bool = False
    facts_block: str = ""  # ready-to-inject prompt text


_BUG_NAME_RE = re.compile(r"^\s*-\s*([A-Za-z][A-Za-z0-9]*)\s*\.?\s*$", re.MULTILINE)


def _parse_bug_names(raw_description: str) -> list[str]:
    """Slither's solc-version raw_description lists bug names as bullet
    lines, e.g. '\\t- VerbatimInvalidDeduplication.'"""
    return _BUG_NAME_RE.findall(raw_description)


def _version_tuple(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in v.split("."))
    except Exception:
        return (0,)


def analyze_solc_version_finding(
    raw_description: str, contract_source: str
) -> CompilerVersionAnalysis:
    """
    Resolve every bug Slither's solc-version detector named against the
    authoritative bug database, and check known trigger conditions against
    the actual contract source. Returns structured facts plus a
    ready-to-inject prompt block — the LLM is told these facts rather than
    asked to reason about compiler-internals it has no way to verify.
    """
    names = _parse_bug_names(raw_description)
    analysis = CompilerVersionAnalysis()
    fixed_versions: list[str] = []

    for name in names:
        meta = _BUGS_BY_NAME.get(name)
        if meta is None:
            analysis.bugs.append(BugApplicability(name=name, known=False))
            continue

        b = BugApplicability(
            name=name,
            known=True,
            introduced=meta.get("introduced"),
            fixed=meta.get("fixed"),
            severity=meta.get("severity"),
            link=meta.get("link"),
            summary=meta.get("summary"),
        )
        if meta.get("fixed"):
            fixed_versions.append(meta["fixed"])

        # Applicability determination, in order of confidence:
        pattern = _MANUAL_TRIGGER_REGEX.get(name) or (
            meta.get("check", {}).get("regex-source")
            if isinstance(meta.get("check"), dict)
            else None
        )
        if pattern:
            triggered = re.search(pattern, contract_source) is not None
            b.applicable = triggered
            b.applicability_reason = (
                f"Source {'contains' if triggered else 'does not contain'} "
                f"the trigger pattern for this bug (checked against "
                f"`{pattern}`)."
            )
        elif meta.get("conditions"):
            # Compiler-setting-dependent (optimizer/viaIR/evmVersion) — not
            # determinable from source alone; say so rather than guess.
            b.applicable = None
            b.applicability_reason = (
                "Depends on compiler build settings "
                f"({meta['conditions']}), not on source code alone — "
                "verify your compiler/build config (foundry.toml, "
                "hardhat.config.js, or solc CLI flags) rather than assume."
            )
        else:
            b.applicable = None
            b.applicability_reason = (
                "No machine-checkable trigger condition on file for this "
                "bug — treat the version range match as the only signal."
            )

        analysis.bugs.append(b)

    analysis.any_confirmed_applicable = any(b.applicable is True for b in analysis.bugs)
    analysis.all_confirmed_not_applicable = bool(analysis.bugs) and all(
        b.applicable is False for b in analysis.bugs
    )
    if fixed_versions:
        analysis.recommended_min_version = max(fixed_versions, key=_version_tuple)

    analysis.facts_block = _render_facts_block(analysis)
    return analysis


def _render_facts_block(analysis: CompilerVersionAnalysis) -> str:
    if not analysis.bugs:
        return "(Could not parse specific bug names from the Slither description.)"

    lines = []
    for b in analysis.bugs:
        if not b.known:
            lines.append(f"- {b.name}: not found in the authoritative bug database.")
            continue
        applic = (
            "CONFIRMED APPLICABLE"
            if b.applicable is True
            else "CONFIRMED NOT APPLICABLE (false positive for this source)"
            if b.applicable is False
            else "UNDETERMINED FROM SOURCE ALONE"
        )
        lines.append(
            f"- {b.name} (introduced {b.introduced or '?'}, fixed in "
            f"{b.fixed or '?'}, upstream severity: {b.severity or '?'}): "
            f"{b.summary or ''} APPLICABILITY: {applic} — {b.applicability_reason}"
        )
    if analysis.recommended_min_version:
        lines.append(
            f"Recommended minimum version to clear ALL listed bugs: "
            f"{analysis.recommended_min_version}"
        )
    if analysis.all_confirmed_not_applicable:
        lines.append(
            "VERDICT: every named bug is confirmed not triggerable by this "
            "specific source. This finding has no practical impact on this "
            "contract as written, even though the declared pragma falls in "
            "the affected version range. Recommend upgrading only as "
            "defense-in-depth, not as fixing an active vulnerability."
        )
    return "\n".join(lines)