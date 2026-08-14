"""Renders an AuditReport into a Markdown document."""
from __future__ import annotations

from datetime import datetime, timezone

from app.chains.schemas import AuditReport

SEVERITY_EMOJI = {
    "Critical": "\U0001F534",
    "High": "\U0001F7E0",
    "Medium": "\U0001F7E1",
    "Low": "\U0001F7E2",
    "Informational": "\u26AA",
}


def render_markdown(report: AuditReport) -> str:
    lines = [
        "# Smart Contract Audit Report",
        "",
        f"**Target:** `{report.target}`  ",
        f"**Generated:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}Z  ",
        f"**Total findings:** {report.total_findings}",
        "",
        "---",
        "",
    ]

    for f in report.findings:
        emoji = SEVERITY_EMOJI.get(f.severity, "\u26AA")
        lines += [
            f"## {emoji} [{f.severity}] Finding `{f.finding_id}`",
            "",
            "**Plain-English explanation:**  ",
            f"{f.plain_explanation}",
            "",
            "**Why it matters:**  ",
            f"{f.why_it_matters}",
            "",
            "**Suggested fix:**",
            "```solidity",
            f"{f.fix_snippet}",
            "```",
            "",
            f"**References:** {', '.join(f.references) if f.references else 'n/a'}",
            "",
            "---",
            "",
        ]

    return "\n".join(lines)
