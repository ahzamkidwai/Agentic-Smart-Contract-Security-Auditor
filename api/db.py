"""
SQLite persistence for past reports (via SQLModel), so the API doesn't
have to re-run analysis every time a report is viewed.
"""
import json
from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel, Field, create_engine, Session

import config
from schemas import ExplainedFinding


class ReportRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: str
    contract_source: str          # filename or project directory analyzed
    findings_json: str
    pdf_path: str


engine = create_engine(f"sqlite:///{config.DB_PATH}")
SQLModel.metadata.create_all(engine)


def save_report_record(contract_source: str, findings: list[ExplainedFinding], pdf_path: str) -> int:
    with Session(engine) as session:
        record = ReportRecord(
            created_at=datetime.now().isoformat(),
            contract_source=contract_source,
            findings_json=json.dumps([f.model_dump() for f in findings], default=str),
            pdf_path=pdf_path,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        return record.id


def get_report_record(report_id: int) -> Optional[ReportRecord]:
    with Session(engine) as session:
        return session.get(ReportRecord, report_id)
