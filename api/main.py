"""
FastAPI app.

Run from the project root with:
    uvicorn api.main:app --reload

Endpoints:
    POST /analyze              upload a single .sol file
    POST /analyze-folder       analyze a directory already on the server (path in body)
    GET  /report/{id}          fetch findings JSON for a past report
    GET  /report/{id}/pdf      download the PDF for a past report
"""
import shutil
import uuid

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import config
from api.pipeline import analyze_contract_file, analyze_contract_folder
from api.db import get_report_record

app = FastAPI(title="Smart Contract Audit Explainer API")


class FolderRequest(BaseModel):
    project_dir: str


@app.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    tmp_path = str(config.UPLOADS_DIR / f"{uuid.uuid4().hex}_{file.filename}")
    with open(tmp_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    final_findings, pdf_path, report_id = analyze_contract_file(tmp_path, file.filename)

    return {
        "report_id": report_id,
        "findings_count": len(final_findings),
        "severities": {f.severity.value: sum(1 for x in final_findings if x.severity == f.severity) for f in final_findings},
    }


@app.post("/analyze-folder")
async def analyze_folder(payload: FolderRequest):
    """
    project_dir must be a path already reachable on the server
    (e.g. a repo the CI job just cloned). For direct browser uploads of
    a whole folder, zip client-side and unzip server-side before calling
    analyze_contract_folder - not implemented here to keep this endpoint simple.
    """
    final_findings, pdf_path, report_id = analyze_contract_folder(payload.project_dir)
    return {
        "report_id": report_id,
        "findings_count": len(final_findings),
    }


@app.get("/report/{report_id}")
async def get_report(report_id: int):
    record = get_report_record(report_id)
    if not record:
        return JSONResponse({"error": "not found"}, status_code=404)
    import json
    return {
        "id": record.id,
        "created_at": record.created_at,
        "contract_source": record.contract_source,
        "findings": json.loads(record.findings_json),
    }


@app.get("/report/{report_id}/pdf")
async def get_report_pdf(report_id: int):
    record = get_report_record(report_id)
    if not record:
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(record.pdf_path, media_type="application/pdf")
