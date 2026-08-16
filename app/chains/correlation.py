"""
Finding correlation.

Multiple Slither detectors can fire on the *same underlying call site* —
e.g. `reentrancy-eth` and `low-level-calls` both flag
`msg.sender.call{value: amount}("")` in VulnerableBank.withdraw(), one for
ordering, one just for being a raw call. Presented as two fully independent
top-level findings, a reader can come away thinking there are two separate
bugs to fix, when really there's one vulnerability (the ordering) and one
purely informational note about the call mechanism.

This module doesn't merge or drop findings — informational findings are
still individually useful (a low-level-calls note is worth having even
when there's no reentrancy present elsewhere) — it links findings that
share flagged source lines within the same file, so the report/PDF can
render "related to finding-N" instead of presenting them as unconnected.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CorrelationGroup:
    finding_ids: list[str]
    shared_lines: set[int]


def _flagged_lines(finding) -> tuple[str | None, set[int]]:
    """Best-effort: pull (filename, line-number set) out of a RawFinding's
    elements. Returns (None, set()) if unavailable."""
    filename = None
    lines: set[int] = set()
    for el in getattr(finding, "elements", []) or []:
        sm = el.get("source_mapping", {})
        fn = sm.get("filename_relative") or sm.get("filename_absolute")
        lo = sm.get("lines")
        if fn:
            filename = fn
        if lo:
            lines.update(lo)
    return filename, lines


def correlate_findings(findings: list) -> dict[str, list[str]]:
    """
    Returns {finding_id: [related_finding_id, ...]} for findings (in the
    same file) whose flagged line sets overlap. Symmetric — if A relates
    to B, B relates to A.
    """
    parsed = []
    for f in findings:
        filename, lines = _flagged_lines(f)
        parsed.append((f.id, filename, lines))

    related: dict[str, set[str]] = {fid: set() for fid, _, _ in parsed}
    for i in range(len(parsed)):
        id_a, file_a, lines_a = parsed[i]
        if not lines_a:
            continue
        for j in range(i + 1, len(parsed)):
            id_b, file_b, lines_b = parsed[j]
            if not lines_b or file_a != file_b:
                continue
            if lines_a & lines_b:
                related[id_a].add(id_b)
                related[id_b].add(id_a)

    return {fid: sorted(ids) for fid, ids in related.items() if ids}