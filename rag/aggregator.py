"""
Merges duplicate findings - Slither and Mythril sometimes flag the same
underlying bug at the same line, and this collapses those into one
entry instead of showing the developer two separate reports for one bug.
"""
from schemas import ExplainedFinding, Severity

SEVERITY_ORDER = {
    Severity.critical: 0,
    Severity.high: 1,
    Severity.medium: 2,
    Severity.low: 3,
    Severity.informational: 4,
    Severity.optimization: 5,
}


def aggregate_findings(explained: list[ExplainedFinding]) -> list[ExplainedFinding]:
    merged: dict[tuple, ExplainedFinding] = {}

    for f in explained:
        key = (f.contract_file, f.line_start, f.swc_id or f.title)
        if key not in merged:
            merged[key] = f
            continue

        existing = merged[key]
        combined_sources = sorted(set(existing.sources + f.sources))
        winner = f if f.confidence > existing.confidence else existing
        winner.sources = combined_sources
        winner.raw_description = (
            f"[Confirmed independently by both {existing.source_tool} and {f.source_tool}] "
            + winner.raw_description
        )
        merged[key] = winner

    result = list(merged.values())
    result.sort(key=lambda x: SEVERITY_ORDER.get(x.severity, 99))
    return result
