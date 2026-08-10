"""
Renders the final Markdown report from a Jinja2 template, then converts
it to PDF via WeasyPrint.
"""
import os
from collections import Counter
from datetime import datetime

import markdown2
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

from schemas import ExplainedFinding

_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
_env = Environment(loader=FileSystemLoader(_TEMPLATE_DIR))


def render_markdown(findings: list[ExplainedFinding], contract_files: list[str]) -> str:
    severity_counts = Counter(f.severity.value for f in findings)
    template = _env.get_template("report.md.j2")
    return template.render(
        findings=findings,
        contract_files=contract_files,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        severity_counts=severity_counts,
    )


def render_pdf(markdown_text: str, output_pdf_path: str) -> str:
    html_body = markdown2.markdown(markdown_text, extras=["tables", "fenced-code-blocks"])
    html_full = f"<html><head><meta charset='utf-8'></head><body>{html_body}</body></html>"
    os.makedirs(os.path.dirname(output_pdf_path), exist_ok=True)
    HTML(string=html_full).write_pdf(output_pdf_path)
    return output_pdf_path


def generate_report(
    findings: list[ExplainedFinding],
    contract_files: list[str],
    output_pdf_path: str,
) -> tuple[str, str]:
    md_text = render_markdown(findings, contract_files)
    pdf_path = render_pdf(md_text, output_pdf_path)
    return md_text, pdf_path
