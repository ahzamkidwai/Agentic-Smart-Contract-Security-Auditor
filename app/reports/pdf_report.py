"""Renders an AuditReport into a PDF using Jinja2 + WeasyPrint."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

from app.chains.schemas import AuditReport
from config import settings

TEMPLATE_DIR = Path(__file__).parent / "templates"

_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))


def render_pdf(report: AuditReport, output_filename: str) -> str:
    template = _env.get_template("report_template.html")
    html_str = template.render(report=report)

    output_path = Path(settings.REPORTS_DIR) / output_filename
    HTML(string=html_str, base_url=str(TEMPLATE_DIR)).write_pdf(str(output_path))
    return str(output_path)
